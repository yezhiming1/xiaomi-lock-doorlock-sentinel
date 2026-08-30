from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import func, select

from doorlock_sentinel.db import Database
from doorlock_sentinel.download_status import (
    STATUS_JOURNAL_NAME,
    DownloadStatusWorker,
)
from doorlock_sentinel.models import Base, DownloadReport, OutboxMessage


def _prepare(settings):
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    return database


def _line(report_key: str, state: str, attempts: int, error_code: str) -> str:
    return json.dumps(
        {
            "attempts": attempts,
            "error_code": error_code,
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "report_key": report_key,
            "schema_version": 1,
            "source": "xiaomi_lock_cloud_backup",
            "state": state,
        }
    )


def test_file_bridge_records_failure_once_and_then_resolution(settings):
    database = _prepare(settings)
    journal = settings.inbox_dir / STATUS_JOURNAL_NAME
    report_key = "a" * 64
    journal.write_text(
        _line(report_key, "failed", 3, "SEGMENT_FETCH_FAILED") + "\n",
        encoding="utf-8",
    )

    worker = DownloadStatusWorker(settings, database)
    assert worker.sync() == 1
    assert worker.sync() == 0
    with database.session() as session:
        row = session.scalar(
            select(DownloadReport).where(DownloadReport.event_digest == report_key)
        )
        assert row is not None and row.state == "failed" and row.notified
        assert session.scalar(select(func.count()).select_from(OutboxMessage)) == 1

    journal.write_text(
        "\n".join(
            [
                _line(report_key, "failed", 3, "SEGMENT_FETCH_FAILED"),
                _line(report_key, "downloaded", 0, "none"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert worker.sync() == 1
    with database.session() as session:
        row = session.scalar(
            select(DownloadReport).where(DownloadReport.event_digest == report_key)
        )
        assert row is not None and row.state == "downloaded" and not row.notified
        assert session.scalar(select(func.count()).select_from(OutboxMessage)) == 1


def test_invalid_status_file_creates_only_fixed_operational_message(settings):
    database = _prepare(settings)
    journal = settings.inbox_dir / STATUS_JOURNAL_NAME
    journal.write_text('{"secret":"must-not-be-echoed"}\n', encoding="utf-8")
    worker = DownloadStatusWorker(settings, database)
    assert worker.sync() == 0
    with database.session() as session:
        message = session.scalar(select(OutboxMessage))
        assert message is not None
        assert message.topic == "system.download_status_invalid"
        assert message.payload == {"error_code": "DOWNLOAD_STATUS_INVALID"}


def test_status_symlink_is_rejected_without_reading_target(settings):
    journal = settings.inbox_dir / STATUS_JOURNAL_NAME
    target = settings.data_dir / "private-status.jsonl"
    target.write_text('not-a-status-file\n', encoding="utf-8")
    try:
        os.symlink(target, journal)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")

    database = _prepare(settings)
    worker = DownloadStatusWorker(settings, database)
    assert worker.sync() == 0
    with database.session() as session:
        message = session.scalar(select(OutboxMessage))
        assert message is not None
        assert message.payload == {"error_code": "DOWNLOAD_STATUS_INVALID"}
