from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from argon2 import PasswordHasher

from doorlock_sentinel.config import Settings
from doorlock_sentinel.db import Database
from doorlock_sentinel.models import Base, Event, VideoIngest


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    inbox = tmp_path / "inbox"
    values = Settings(
        environment="test",
        data_dir=data,
        inbox_dir=inbox,
        derived_dir=data / "derived",
        export_dir=data / "exports",
        models_dir=data / "models",
        runtime_dir=tmp_path / "run",
        database_url=f"sqlite:///{data / 'test.sqlite3'}",
        internal_api_secret="test-internal-secret",
        web_password_hash=PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=2,
            hash_len=32,
            salt_len=16,
        ).hash("correct horse battery staple"),
        security_pepper="test-security-pepper",
        face_backend="mock",
        embedding_dimension=4,
        model_id="test-model-v1",
        cluster_review_events=3,
        cluster_review_days=2,
        cluster_review_tracks=3,
        stable_seconds=0,
        trusted_person_events=5,
        trusted_person_days=3,
        trusted_hosts="testserver,localhost,127.0.0.1",
        public_base_url="http://testserver",
        scan_interval_seconds=0.05,
        manifest_export_seconds=10,
    )
    values.ensure_writable_directories()
    inbox.mkdir(parents=True, exist_ok=True)
    values.models_dir.mkdir(parents=True, exist_ok=True)
    return values


@pytest.fixture
def database(settings: Settings) -> Database:
    db = Database(settings)
    Base.metadata.create_all(db.engine)
    try:
        yield db
    finally:
        db.engine.dispose()


def create_event(session, index: int, occurred_at: datetime | None = None) -> Event:
    ingest = VideoIngest(
        fingerprint=f"fingerprint-{index}",
        source_path=f"/tmp/video-{index}.mp4",
        original_name=f"video-{index}.mp4",
        size_bytes=100,
        mtime_ns=index,
        state="processed",
    )
    session.add(ingest)
    session.flush()
    event = Event(
        video_ingest_id=ingest.id,
        occurred_at=occurred_at or datetime(2026, 8, 29, 10, index, tzinfo=timezone.utc),
        duration_seconds=5,
    )
    session.add(event)
    session.flush()
    ingest.event_id = event.id
    return event
