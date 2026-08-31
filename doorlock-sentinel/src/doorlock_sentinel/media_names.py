from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
LEGACY_VIDEO_RE = re.compile(
    r"^xiaomi_lock_(?P<stamp>\d{8}T\d{9}Z)_(?P<digest>[0-9a-f]{12})\.mp4$",
    re.IGNORECASE,
)
LEGACY_TIME_RE = re.compile(
    r"^xiaomi_lock_(?P<stamp>\d{8}T\d{9}Z)_[0-9a-f]+\.(?:mp4|mov|mkv|avi|m4v)$",
    re.IGNORECASE,
)
CURRENT_VIDEO_RE = re.compile(
    r"^xiaomi_lock_(?P<stamp>\d{8}T\d{6})(?:-(?P<sequence>\d{2,3}))?\.mp4$",
    re.IGNORECASE,
)


def legacy_event_time_ms(name: str) -> int | None:
    match = LEGACY_TIME_RE.fullmatch(name)
    if not match:
        return None
    try:
        parsed = datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%S%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def current_video_filename(event_time_ms: int, sequence: int = 1) -> str:
    if event_time_ms < 0 or not 1 <= sequence <= 999:
        raise ValueError("invalid video filename input")
    stamp = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc).astimezone(
        BEIJING
    ).strftime("%Y%m%dT%H%M%S")
    suffix = "" if sequence == 1 else f"-{sequence:02d}"
    return f"xiaomi_lock_{stamp}{suffix}.mp4"


def build_legacy_video_mapping(names: list[str]) -> dict[str, str]:
    occupied = {name for name in names if CURRENT_VIDEO_RE.fullmatch(name)}
    groups: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for name in names:
        if not LEGACY_VIDEO_RE.fullmatch(name):
            continue
        event_time_ms = legacy_event_time_ms(name)
        if event_time_ms is None:
            continue
        base = current_video_filename(event_time_ms)
        groups[base].append((event_time_ms, name))
    mapping: dict[str, str] = {}
    for base in sorted(groups):
        for event_time_ms, name in sorted(groups[base]):
            for sequence in range(1, 1000):
                candidate = current_video_filename(event_time_ms, sequence)
                if candidate not in occupied:
                    mapping[name] = candidate
                    occupied.add(candidate)
                    break
            else:
                raise ValueError("video filename collision capacity reached")
    return mapping


def occurred_at_from_filename(path: Path) -> datetime | None:
    current = CURRENT_VIDEO_RE.fullmatch(path.name)
    if current:
        try:
            local = datetime.strptime(current.group("stamp"), "%Y%m%dT%H%M%S").replace(
                tzinfo=BEIJING
            )
        except ValueError:
            return None
        return local.astimezone(timezone.utc)
    legacy_ms = legacy_event_time_ms(path.name)
    if legacy_ms is None:
        return None
    return datetime.fromtimestamp(legacy_ms / 1000, tz=timezone.utc)


def is_current_video_filename(video_name: str) -> bool:
    return bool(CURRENT_VIDEO_RE.fullmatch(video_name))


def derived_image_name(video_name: str, track_index: int, kind: str) -> str:
    if not CURRENT_VIDEO_RE.fullmatch(video_name):
        raise ValueError("derived images require a current video filename")
    if track_index < 0 or track_index > 998:
        raise ValueError("invalid track index")
    if kind not in {"face", "scene"}:
        raise ValueError("invalid derived image kind")
    marker = "a" if kind == "face" else "b"
    return f"{Path(video_name).stem}-{marker}{track_index + 1:03d}.jpg"
