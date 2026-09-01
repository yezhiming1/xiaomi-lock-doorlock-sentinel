from __future__ import annotations

from datetime import datetime, timedelta, timezone

from doorlock_sentinel.web_data import _iso


def test_web_timestamp_is_explicit_utc_for_sqlite_naive_values():
    assert _iso(datetime(2026, 9, 1, 8, 30, 45)) == "2026-09-01T08:30:45Z"


def test_web_timestamp_normalizes_aware_values_and_preserves_none():
    beijing = timezone(timedelta(hours=8))

    assert _iso(datetime(2026, 9, 1, 16, 30, 45, tzinfo=beijing)) == "2026-09-01T08:30:45Z"
    assert _iso(None) is None
