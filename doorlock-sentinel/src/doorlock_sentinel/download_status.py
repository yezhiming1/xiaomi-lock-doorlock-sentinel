from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import Database
from .models import DownloadReport, ensure_utc
from .outbox import enqueue

logger = logging.getLogger(__name__)

STATUS_JOURNAL_NAME = ".xiaomi_lock_backup_status.jsonl"
MAX_STATUS_JOURNAL_BYTES = 16 * 1024 * 1024
MAX_STATUS_LINE_BYTES = 2048
_STATES = {"discovered", "retrying", "downloaded", "failed"}


@dataclass(frozen=True, slots=True)
class StatusReport:
    report_key: str
    recorded_at: datetime | None
    state: str
    attempts: int
    error_code: str | None
    next_retry_at: datetime | None = None


def record_status_report(session: Session, report: StatusReport) -> DownloadReport:
    row = session.scalar(
        select(DownloadReport).where(
            DownloadReport.event_digest == report.report_key
        )
    )
    if row is None:
        row = DownloadReport(
            event_digest=report.report_key,
            event_time=report.recorded_at,
            state=report.state,
            attempts=report.attempts,
            error_code=report.error_code,
            next_retry_at=report.next_retry_at,
        )
        session.add(row)
        session.flush()
    else:
        row.event_time = report.recorded_at
        row.state = report.state
        row.attempts = report.attempts
        row.error_code = report.error_code
        row.next_retry_at = report.next_retry_at
    if report.state == "failed" and not row.notified:
        enqueue(
            session,
            topic="system.download_failed",
            dedupe_key=f"download-failed:{report.report_key}",
            priority=100,
            payload={
                "event_digest": report.report_key,
                "event_time": (
                    report.recorded_at.isoformat() if report.recorded_at else None
                ),
                "attempts": report.attempts,
                "error_code": report.error_code,
            },
        )
        row.notified = True
    elif report.state == "downloaded":
        row.notified = False
    return row


def read_status_journal(path: Path) -> list[StatusReport]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return []
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError("status_journal_unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_STATUS_JOURNAL_BYTES
        ):
            raise ValueError("status_journal_unsafe")
        chunks: list[bytes] = []
        remaining = MAX_STATUS_JOURNAL_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_STATUS_JOURNAL_BYTES:
        raise ValueError("status_journal_too_large")

    reports: dict[str, StatusReport] = {}
    for line in raw.splitlines():
        if not line:
            continue
        if len(line) > MAX_STATUS_LINE_BYTES:
            raise ValueError("status_line_too_large")
        try:
            value = json.loads(line)
            report = _parse_report(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            raise ValueError("status_line_invalid") from None
        reports[report.report_key] = report
    return list(reports.values())


def _parse_report(value: object) -> StatusReport:
    if not isinstance(value, dict):
        raise ValueError("status_line_invalid")
    report_key = value.get("report_key")
    state = value.get("state")
    attempts = value.get("attempts")
    error_code = value.get("error_code")
    if (
        value.get("schema_version") != 1
        or value.get("source") != "xiaomi_lock_cloud_backup"
        or not _is_digest(report_key)
        or state not in _STATES
        or not isinstance(attempts, int)
        or isinstance(attempts, bool)
        or not 0 <= attempts <= 100
        or (error_code is not None and not _is_code(error_code))
    ):
        raise ValueError("status_line_invalid")
    recorded_at = _parse_datetime(value.get("recorded_at"))
    return StatusReport(
        report_key=report_key,
        recorded_at=recorded_at,
        state=state,
        attempts=attempts,
        error_code=error_code,
    )


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("status_time_invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("status_time_invalid")
    return ensure_utc(parsed)


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _is_code(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 96 and all(
        character.isascii() and (character.isalnum() or character == "_")
        for character in value
    )


class DownloadStatusWorker:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.path = settings.inbox_dir / STATUS_JOURNAL_NAME
        self._last_signature: tuple[int, int] | None = None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def sync(self) -> int:
        try:
            metadata = self.path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return 0
        signature = (metadata.st_size, metadata.st_mtime_ns)
        if signature == self._last_signature:
            return 0
        try:
            reports = read_status_journal(self.path)
        except (OSError, ValueError):
            with self.database.session() as session:
                enqueue(
                    session,
                    topic="system.download_status_invalid",
                    dedupe_key=f"download-status-invalid:{metadata.st_size}:{metadata.st_mtime_ns}",
                    priority=100,
                    payload={"error_code": "DOWNLOAD_STATUS_INVALID"},
                )
            logger.error("download status journal rejected code=DOWNLOAD_STATUS_INVALID")
            self._last_signature = signature
            return 0
        with self.database.session() as session:
            for report in reports:
                record_status_report(session, report)
        self._last_signature = signature
        return len(reports)

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.sync)
            except Exception:
                logger.error(
                    "download status synchronization failed code=DOWNLOAD_STATUS_SYNC_FAILED"
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.scan_interval_seconds,
                )
