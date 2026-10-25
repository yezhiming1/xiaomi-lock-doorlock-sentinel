"""Credential-free client for the isolated cloud resolver's Unix socket."""
from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path
from urllib.parse import urlsplit


def validate_playback_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not host.endswith(".115cdn.net")
        or parsed.username or parsed.password or parsed.port not in (None, 443)
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("untrusted playback URL")
    return value


def resolve_video(socket_path: Path, remote_path: str, size: int, user_agent: str) -> str:
    connection = http.client.HTTPConnection("localhost", timeout=45)
    stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stream.settimeout(45)
    try:
        stream.connect(str(socket_path))
        connection.sock = stream
        body = json.dumps({"path": remote_path, "size": size, "user_agent": user_agent})
        connection.request("POST", "/resolve", body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(8193)
        if response.status != 200 or len(raw) > 8192:
            raise ValueError("resolver unavailable")
        result = json.loads(raw)
        return validate_playback_url(result["url"])
    finally:
        connection.close()
        stream.close()
