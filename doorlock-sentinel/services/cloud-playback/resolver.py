"""Isolated 115 resolver. No media proxy, public listener, or file mutation API."""
from __future__ import annotations

import argparse
import json
import os
import socketserver
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path, PurePosixPath


class ResolverError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ResolverError("provider_redirect")


def atomic_save(path, value):
    temp = path.with_suffix(".pending")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Resolver:
    def __init__(self, state_path, project_root):
        self.state_path = Path(state_path)
        self.root = PurePosixPath(project_root)
        if not self.root.is_absolute() or self.root == PurePosixPath("/"):
            raise ValueError("Project root must be bounded")
        self.state = json.loads(self.state_path.read_text())
        self.next_call = 0.0
        self.cache = {}
        self.cooldown_until = 0.0
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, endpoint, fields, ua, *, refresh=False):
        if time.monotonic() < self.cooldown_until:
            raise ResolverError("provider_cooldown")
        # Single-threaded server plus a monotonic pacer: one API call at a time.
        time.sleep(max(0, self.next_call - time.monotonic()))
        self.next_call = time.monotonic() + 0.55
        headers = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": ua}
        host = "https://qrcodeapi.115.com" if refresh else "https://proapi.115.com"
        if not refresh:
            headers["Authorization"] = "Bearer " + self.state["access_token"]
        request = urllib.request.Request(
            host + endpoint, data=urllib.parse.urlencode(fields).encode(),
            headers=headers, method="POST",
        )
        try:
            with self.opener.open(request, timeout=12) as response:
                raw = response.read(1048577)
            if len(raw) > 1048576:
                raise ResolverError("response_too_large")
            body = json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                self.cooldown_until = time.monotonic() + 300
            raise ResolverError("provider_unavailable") from exc
        except (OSError, ValueError) as exc:
            raise ResolverError("provider_unavailable") from exc
        if not body.get("state"):
            self.cooldown_until = time.monotonic() + 60
            raise ResolverError("provider_rejected")
        return body["data"]

    def ensure_token(self):
        if self.state.get("expires_at", 0) > time.time() + 120:
            return
        intent = self.state_path.with_suffix(".refresh-intent")
        if intent.exists():
            raise ResolverError("refresh_requires_reconciliation")
        atomic_save(intent, {"started_at": time.time()})
        # An ambiguous refresh is never retried automatically: a rotated token
        # could already exist at the provider. Preserve intent for reconciliation.
        data = self.request("/open/refreshToken", {
            "refresh_token": self.state["refresh_token"],
        }, "Doorlock-Cloud-Playback/0.1.7", refresh=True)
        if not data.get("access_token") or not data.get("refresh_token"):
            raise ResolverError("invalid_refresh_response")
        candidate = {"access_token": data["access_token"],
                     "refresh_token": data["refresh_token"],
                     "expires_at": time.time() + int(data["expires_in"])}
        atomic_save(self.state_path, candidate)
        self.state = json.loads(self.state_path.read_text())
        if self.state != candidate:
            raise ResolverError("token_readback_failed")
        intent.unlink()

    def resolve(self, request):
        remote = request.get("path", "")
        path = PurePosixPath(remote)
        ua = request.get("user_agent", "")
        size = request.get("size")
        if (
            not isinstance(remote, str) or len(remote) > 1000
            or not path.is_relative_to(self.root) or ".." in path.parts
            or path.suffix.lower() != ".mp4" or str(path) != remote
            or not isinstance(size, int) or isinstance(size, bool) or size <= 0
            or not isinstance(ua, str) or not ua or len(ua) > 512
            or any(ord(c) < 32 for c in ua)
        ):
            raise ResolverError("invalid_request")
        cached = self.cache.get((remote, size, ua))
        if cached and cached[0] > time.monotonic():
            return cached[1]
        self.ensure_token()
        info = self.request("/open/folder/get_info", {"path": remote}, ua)
        if isinstance(info, list):
            if len(info) != 1:
                raise ResolverError("file_not_found")
            info = info[0]
        if info.get("file_name") != path.name or int(info.get("size_byte", -1)) != size:
            raise ResolverError("receipt_mismatch")
        data = self.request("/open/ufile/downurl", {"pick_code": info["pick_code"]}, ua)
        if not isinstance(data, dict) or len(data) != 1:
            raise ResolverError("invalid_link_response")
        url = next(iter(data.values()))["url"]["url"]
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https" or not (parsed.hostname or "").endswith(".115cdn.net")
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or any(ord(c) < 32 for c in url)
        ):
            raise ResolverError("untrusted_link")
        if len(self.cache) >= 1024:
            self.cache.clear()
        self.cache[(remote, size, ua)] = (time.monotonic() + 45, url)
        return url


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Neither signed URLs nor user-provided paths may enter access logs.

    def do_POST(self):
        try:
            self.connection.settimeout(5)
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/resolve" or not 0 < length <= 8192:
                raise ResolverError("invalid_request")
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ResolverError("invalid_request")
            body = {"url": self.server.resolver.resolve(request)}
            status = 200
        except (ResolverError, KeyError, TypeError, ValueError, OSError):
            body = {"error": "cloud_playback_unavailable"}
            status = 503
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def main():
    import fcntl

    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    path = Path(args.socket)
    with path.with_suffix(".lock").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Only remove this exact stale socket after exclusive ownership is proven.
        if path.exists():
            if not path.is_socket():
                raise RuntimeError("Unexpected socket-path object")
            path.unlink()
        resolver = Resolver(args.state, args.project_root)
        with socketserver.UnixStreamServer(str(path), Handler) as server:
            server.resolver = resolver
            os.chmod(path, 0o660)
            os.chown(path, 10001, 10001)
            try:
                server.serve_forever()
            finally:
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
