from __future__ import annotations

import asyncio
import logging
import os
import socket
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from sqlalchemy import or_, select

from .artifacts import file_sha256, safe_artifact_path
from .config import Settings
from .db import Database
from .models import IngestState, VideoIngest, utcnow
from .outbox import enqueue
from .pipeline import ProcessingPipeline, fingerprint

logger = logging.getLogger(__name__)


class IngestWorker:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        pipeline: ProcessingPipeline,
    ):
        self.settings = settings
        self.database = database
        self.pipeline = pipeline
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:ingest"
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def recover_stale(self) -> int:
        now = utcnow()
        recovered = 0
        with self.database.session() as session:
            rows = list(
                session.scalars(
                    select(VideoIngest).where(
                        VideoIngest.state == IngestState.PROCESSING.value,
                        VideoIngest.lease_until < now,
                    )
                )
            )
            for row in rows:
                row.state = IngestState.RETRY.value
                row.available_at = now
                row.lease_owner = None
                row.lease_until = None
                row.last_error_code = "expired_processing_lease"
                row.last_error = "任务租约已过期，已安全恢复等待重试"
                recovered += 1
        return recovered

    def discover(self) -> int:
        now_timestamp = utcnow().timestamp()
        discovered = 0
        if not self.settings.inbox_dir.is_dir():
            return 0
        for path in sorted(self.settings.inbox_dir.rglob("*")):
            if path.suffix.lower() not in self.settings.extensions:
                continue
            try:
                path = safe_artifact_path(self.settings, path)
                stat = path.stat()
            except (FileNotFoundError, ValueError):
                continue
            if stat.st_size <= 0 or now_timestamp - stat.st_mtime < self.settings.stable_seconds:
                continue
            key = fingerprint(path)
            with self.database.session() as session:
                if session.scalar(
                    select(VideoIngest.id).where(VideoIngest.fingerprint == key)
                ):
                    continue
                session.add(
                    VideoIngest(
                        fingerprint=key,
                        source_path=str(path.resolve()),
                        original_name=path.name,
                        size_bytes=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                    )
                )
                discovered += 1
        return discovered

    def claim_one(self) -> str | None:
        now = utcnow()
        with self.database.session() as session:
            row = session.scalar(
                select(VideoIngest)
                .where(
                    or_(
                        VideoIngest.state == IngestState.DISCOVERED.value,
                        VideoIngest.state == IngestState.RETRY.value,
                        (
                            (VideoIngest.state == IngestState.PROCESSING.value)
                            & (VideoIngest.lease_until < now)
                        ),
                    ),
                    or_(
                        VideoIngest.available_at <= now,
                        VideoIngest.retry_requested.is_(True),
                    ),
                )
                .order_by(
                    VideoIngest.retry_requested.desc(),
                    VideoIngest.created_at.asc(),
                )
                .limit(1)
            )
            if not row:
                return None
            row.state = IngestState.PROCESSING.value
            row.lease_owner = self.worker_id
            row.lease_until = now + timedelta(
                seconds=self.settings.processing_lease_seconds
            )
            row.retry_requested = False
            row.attempts += 1
            return row.id

    def _prepare_hash(self, ingest_id: str) -> bool:
        with self.database.session() as session:
            row = session.get(VideoIngest, ingest_id)
            if not row:
                raise ValueError("ingest record disappeared")
            path = Path(row.source_path)
            digest = row.sha256
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = digest or file_sha256(path)
        with self.database.session() as session:
            row = session.get(VideoIngest, ingest_id)
            if not row:
                raise ValueError("ingest record disappeared")
            duplicate = session.scalar(
                select(VideoIngest).where(
                    VideoIngest.id != row.id,
                    VideoIngest.sha256 == digest,
                    VideoIngest.state.in_(
                        [IngestState.PROCESSED.value, IngestState.DUPLICATE.value]
                    ),
                )
            )
            row.sha256 = digest
            if not duplicate:
                return True
            row.state = IngestState.DUPLICATE.value
            row.duplicate_of_id = duplicate.duplicate_of_id or duplicate.id
            row.event_id = duplicate.event_id
            row.completed_at = utcnow()
            row.lease_owner = None
            row.lease_until = None
            return False

    def process_one(self, ingest_id: str) -> None:
        if self._prepare_hash(ingest_id):
            self.pipeline.process(ingest_id)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        name = type(exc).__name__.lower()
        if "model" in name:
            return "model_unavailable"
        if isinstance(exc, FileNotFoundError):
            return "source_missing"
        return "analysis_failed"

    def fail(self, ingest_id: str, exc: Exception) -> None:
        schedule = self.settings.retry_schedule
        error_code = self._error_code(exc)
        with self.database.session() as session:
            row = session.get(VideoIngest, ingest_id)
            if not row:
                return
            row.last_error_code = error_code
            row.last_error = {
                "model_unavailable": "识别模型当前不可用",
                "source_missing": "源录像当前不可用",
                "analysis_failed": "录像分析未成功完成",
            }[error_code]
            row.lease_owner = None
            row.lease_until = None
            retry_index = row.attempts - 1
            if retry_index < len(schedule):
                row.state = IngestState.RETRY.value
                row.available_at = utcnow() + timedelta(seconds=schedule[retry_index])
            else:
                row.state = IngestState.DEAD.value
                if (
                    self.settings.failure_notifications_enabled
                    and not row.failure_notified
                ):
                    enqueue(
                        session,
                        topic="system.analysis_failed",
                        dedupe_key=f"analysis-dead:{row.id}",
                        priority=95,
                        payload={
                            "ingest_id": row.id,
                            "file_name": row.original_name,
                            "error_code": row.last_error_code,
                            "attempts": row.attempts,
                        },
                    )
                    row.failure_notified = True
        logger.error("video analysis failed code=VIDEO_ANALYSIS_FAILED")

    def request_retry(self, ingest_id: str) -> bool:
        with self.database.session() as session:
            row = session.get(VideoIngest, ingest_id)
            if not row or row.state not in {
                IngestState.RETRY.value,
                IngestState.DEAD.value,
            }:
                return False
            row.state = IngestState.RETRY.value
            row.retry_requested = True
            row.available_at = utcnow()
            row.failure_notified = False
            return True

    async def run(self) -> None:
        await asyncio.to_thread(self.recover_stale)
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.discover)
                if self.pipeline.ready:
                    ingest_id = await asyncio.to_thread(self.claim_one)
                    if ingest_id:
                        try:
                            await asyncio.to_thread(self.process_one, ingest_id)
                        except Exception as exc:
                            await asyncio.to_thread(self.fail, ingest_id, exc)
                        continue
            except Exception:
                logger.error("ingest loop failure code=INGEST_LOOP_FAILED")
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.scan_interval_seconds,
                )
