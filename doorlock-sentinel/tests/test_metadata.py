import json
from datetime import timezone

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
