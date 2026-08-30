from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


try:
    with urllib.request.urlopen("http://127.0.0.1:8787/health/ready", timeout=4) as response:
        payload = json.loads(response.read())
        if response.status != 200 or payload.get("status") != "ready":
            fail("recognition service is not ready")
except (OSError, ValueError, urllib.error.URLError) as exc:
    fail(f"recognition readiness check failed: {type(exc).__name__}")

if os.environ.get("WECOM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
    path = Path(os.environ.get("WECOM_HEARTBEAT_PATH", "/run/doorlock/wecom-heartbeat.json"))
    if not path.is_file() or time.time() - path.stat().st_mtime > 45:
        fail("WeCom heartbeat is missing or stale")
    try:
        heartbeat = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fail("WeCom heartbeat is invalid")
    if not heartbeat.get("connected"):
        fail("WeCom WebSocket is not authenticated")

print("healthy")
