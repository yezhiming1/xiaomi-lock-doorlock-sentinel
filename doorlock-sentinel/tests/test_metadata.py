import json
from datetime import datetime, timezone

from doorlock_sentinel.metadata import read_sidecar


def test_sidecar_contract(tmp_path):
    video = tmp_path / "event.mp4"
    video.write_bytes(b"not-a-real-video")
    video.with_suffix(".mp4.json").write_text(
        json.dumps(
            {
                "occurred_at": "2026-08-29T18:42:13+08:00",
                "event_type": "someone_at_door",
                "failed_unlock": True,
                "custom_field": "kept",
            }
        ),
        encoding="utf-8",
    )
    result = read_sidecar(video)
    assert result.occurred_at.astimezone(timezone.utc).hour == 10
    assert result.failed_unlock is True
    assert result.extra["custom_field"] == "kept"


def test_current_and_legacy_downloader_names_preserve_event_time(tmp_path):
    current = tmp_path / "xiaomi_lock_20260829T184213.mp4"
    legacy = tmp_path / "xiaomi_lock_20260829T104213123Z_deadbeef.mp4"
    current.write_bytes(b"fixture")
    legacy.write_bytes(b"fixture")

    current_result = read_sidecar(current)
    legacy_result = read_sidecar(legacy)

    assert current_result.time_source == "downloader_filename"
    assert current_result.occurred_at == datetime(
        2026, 8, 29, 10, 42, 13, tzinfo=timezone.utc
    )
    assert legacy_result.time_source == "downloader_filename"
    assert legacy_result.occurred_at == datetime(
        2026, 8, 29, 10, 42, 13, 123000, tzinfo=timezone.utc
    )
