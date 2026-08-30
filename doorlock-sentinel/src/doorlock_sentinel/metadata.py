from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DOWNLOADER_NAME = re.compile(
    r"^xiaomi_lock_(?P<stamp>\d{8}T\d{9}Z)_[0-9a-f]+\.(?:mp4|mov|mkv|avi|m4v)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class EventMetadata:
    occurred_at: datetime
    downloaded_at: datetime | None = None
    time_source: str = "explicit"
    source: str = "xiaomi_lock"
    event_type: str = "video"
    event_id: str | None = None
    unlock_method: str | None = None
    operation_id: str | None = None
    operation_user: str | None = None
    failed_unlock: bool = False
    tamper_alarm: bool = False
    doorbell: bool = False
    touched_handle: bool = False
    approach_door: bool = False
    passerby_only: bool = False
    package_delivery: bool = False
    repeated_return: bool = False
    dwell_seconds: float | None = None
    role_hint: str | None = None
    zones: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result = {
            "occurred_at": self.occurred_at.isoformat(),
            "downloaded_at": (
                self.downloaded_at.isoformat() if self.downloaded_at else None
            ),
            "time_source": self.time_source,
            "source": self.source,
            "event_type": self.event_type,
            "event_id": self.event_id,
            "unlock_method": self.unlock_method,
            "operation_id": self.operation_id,
            "operation_user": self.operation_user,
            "failed_unlock": self.failed_unlock,
            "tamper_alarm": self.tamper_alarm,
            "doorbell": self.doorbell,
            "touched_handle": self.touched_handle,
            "approach_door": self.approach_door,
            "passerby_only": self.passerby_only,
            "package_delivery": self.package_delivery,
            "repeated_return": self.repeated_return,
            "dwell_seconds": self.dwell_seconds,
            "role_hint": self.role_hint,
            "zones": self.zones,
        }
        result.update(self.extra)
        return result


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, str) and value:
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    return None


def _time_from_filename(path: Path) -> datetime | None:
    match = _DOWNLOADER_NAME.match(path.name)
    if not match:
        return None
    stamp = match.group("stamp")
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%S%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _read_sidecar_payload(video_path: Path) -> dict[str, Any]:
    candidates = [
        video_path.with_suffix(video_path.suffix + ".json"),
        video_path.with_suffix(".json"),
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def read_sidecar(video_path: Path) -> EventMetadata:
    downloaded_at = datetime.fromtimestamp(video_path.stat().st_mtime, tz=timezone.utc)
    payload = _read_sidecar_payload(video_path)
    sidecar_time = _parse_time(payload.get("occurred_at", payload.get("time")))
    filename_time = _time_from_filename(video_path)
    if sidecar_time:
        occurred_at = sidecar_time
        time_source = "sidecar"
    elif filename_time:
        occurred_at = filename_time
        time_source = "downloader_filename"
    else:
        occurred_at = downloaded_at
        time_source = "file_mtime_fallback"
    known = {
        "occurred_at",
        "time",
        "source",
        "event_type",
        "event_id",
        "unlock_method",
        "operation_id",
        "operation_user",
        "failed_unlock",
        "tamper_alarm",
        "doorbell",
        "touched_handle",
        "approach_door",
        "passerby_only",
        "package_delivery",
        "repeated_return",
        "dwell_seconds",
        "role_hint",
        "zones",
    }
    dwell = payload.get("dwell_seconds")
    return EventMetadata(
        occurred_at=occurred_at,
        downloaded_at=downloaded_at,
        time_source=time_source,
        source=str(payload.get("source", "xiaomi_lock")),
        event_type=str(payload.get("event_type", "video")),
        event_id=str(payload["event_id"]) if payload.get("event_id") else None,
        unlock_method=(
            str(payload["unlock_method"]) if payload.get("unlock_method") else None
        ),
        operation_id=(
            str(payload["operation_id"]) if payload.get("operation_id") else None
        ),
        operation_user=(
            str(payload["operation_user"]) if payload.get("operation_user") else None
        ),
        failed_unlock=bool(payload.get("failed_unlock", False)),
        tamper_alarm=bool(payload.get("tamper_alarm", False)),
        doorbell=bool(payload.get("doorbell", False)),
        touched_handle=bool(payload.get("touched_handle", False)),
        approach_door=bool(payload.get("approach_door", False)),
        passerby_only=bool(payload.get("passerby_only", False)),
        package_delivery=bool(payload.get("package_delivery", False)),
        repeated_return=bool(payload.get("repeated_return", False)),
        dwell_seconds=float(dwell) if dwell is not None else None,
        role_hint=str(payload["role_hint"]) if payload.get("role_hint") else None,
        zones=[
            str(item)
            for item in payload.get("zones", [])
            if isinstance(item, (str, int))
        ],
        extra={key: value for key, value in payload.items() if key not in known},
    )
