#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import select

from doorlock_sentinel.artifacts import apply_backup_receipt, file_sha256
from doorlock_sentinel.config import Settings
from doorlock_sentinel.db import Database
from doorlock_sentinel.ingest import IngestWorker
from doorlock_sentinel.models import ArtifactManifest, Base, Event, UnknownCluster
from doorlock_sentinel.people import label_cluster
from doorlock_sentinel.pipeline import ProcessingPipeline


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="doorlock-smoke-") as directory:
        root = Path(directory)
        inbox = root / "inbox"
        data = root / "data"
        inbox.mkdir()
        video = inbox / "xiaomi_lock_20260829T144800000Z_deadbeef.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=gray:s=640x360:d=3",
                "-r",
                "15",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ],
            check=True,
        )
        settings = Settings(
            environment="test",
            data_dir=data,
            inbox_dir=inbox,
            derived_dir=data / "derived",
            export_dir=data / "exports",
            models_dir=root / "models",
            runtime_dir=root / "run",
            database_url=f"sqlite:///{data / 'smoke.sqlite3'}",
            internal_api_secret="smoke-internal",
            web_password_hash="smoke-hash",
            security_pepper="smoke-pepper",
            face_backend="mock",
            embedding_dimension=4,
            model_id="smoke-model-v1",
            stable_seconds=0,
            identity_notifications_enabled=False,
            risk_notifications_enabled=False,
        )
        settings.ensure_writable_directories()
        database = Database(settings)
        Base.metadata.create_all(database.engine)
        pipeline = ProcessingPipeline(settings, database)
        worker = IngestWorker(settings, database, pipeline)
        assert worker.discover() == 1
        ingest_id = worker.claim_one()
        assert ingest_id
        worker.process_one(ingest_id)
        with database.session() as session:
            event = session.scalar(select(Event))
            cluster = session.scalar(select(UnknownCluster))
            assert event and event.track_count == 1
            assert cluster
            labeled = label_cluster(
                session,
                settings,
                cluster_id=cluster.id,
                display_name="合成测试人物",
                relationship="other",
                idempotency_key="smoke-label-0001",
            )
            assert labeled["prototype_count"] == 1
            source = session.get(ArtifactManifest, event.source_artifact_id)
            assert source
            receipt = apply_backup_receipt(
                session,
                artifact_id=source.id,
                state="verified",
                remote_sha256=file_sha256(video),
                remote_size_bytes=video.stat().st_size,
                remote_locator="synthetic/smoke.mp4",
                receipt_source="smoke",
            )
            assert receipt.state == "verified"
        assert video.is_file()
        database.engine.dispose()
        print("SMOKE_PASS events=1 tracks=1 labeled=1 backup_receipt=verified")


if __name__ == "__main__":
    main()
