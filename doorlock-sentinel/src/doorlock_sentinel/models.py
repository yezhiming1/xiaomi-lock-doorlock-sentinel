from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class Base(DeclarativeBase):
    pass


class IngestState(str, enum.Enum):
    DISCOVERED = "discovered"
    PROCESSING = "processing"
    RETRY = "retry"
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    DEAD = "dead"


class OutboxState(str, enum.Enum):
    PENDING = "pending"
    LEASED = "leased"
    SENT = "sent"
    DEAD = "dead"


class ArtifactState(str, enum.Enum):
    RECEIPT_PENDING = "receipt_pending"
    VERIFIED = "verified"
    MISSING = "missing"


class VideoIngest(Base):
    __tablename__ = "video_ingest"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("vid"))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mtime_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(
        String(24), default=IngestState.DISCOVERED.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(96))
    last_error: Mapped[str | None] = mapped_column(Text)
    failure_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    event_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    duplicate_of_id: Mapped[str | None] = mapped_column(String(64))
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArtifactManifest(Base):
    __tablename__ = "artifact_manifest"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("art"))
    artifact_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    logical_path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    state: Mapped[str] = mapped_column(
        String(24), default=ArtifactState.RECEIPT_PENDING.value, index=True
    )
    retention_class: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    retention_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    backup_state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    backup_receipt_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("evt"))
    video_ingest_id: Mapped[str] = mapped_column(ForeignKey("video_ingest.id"), unique=True)
    source_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifact_manifest.id"), unique=True
    )
    external_event_id: Mapped[str | None] = mapped_column(String(160), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(64), default="xiaomi_lock")
    event_type: Mapped[str] = mapped_column(String(64), default="video", index=True)
    unlock_method: Mapped[str | None] = mapped_column(String(96))
    operation_user: Mapped[str | None] = mapped_column(String(96))
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis_state: Mapped[str] = mapped_column(String(24), default="complete", index=True)
    track_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_face_count: Mapped[int] = mapped_column(Integer, default=0)
    risk_score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="record")
    risk_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("person")
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    relationship: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="provisional", index=True)
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("persons.id"), index=True)
    matched_events: Mapped[int] = mapped_column(Integer, default=0)
    distinct_days: Mapped[int] = mapped_column(Integer, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PersonIndex(Base):
    __tablename__ = "person_index"

    person_id: Mapped[str] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True
    )
    model_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    centroid: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    prototype_count: Mapped[int] = mapped_column(Integer, default=0)
    search_prototype_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FaceTrack(Base):
    __tablename__ = "face_tracks"
    __table_args__ = (UniqueConstraint("event_id", "track_index"),)

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("track")
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    track_index: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, default=1)
    representative: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    first_timestamp: Mapped[float] = mapped_column(Float, default=0.0)
    last_timestamp: Mapped[float] = mapped_column(Float, default=0.0)
    best_bbox_json: Mapped[list[int]] = mapped_column(JSON, default=list)
    best_face_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifact_manifest.id")
    )
    best_frame_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifact_manifest.id")
    )
    decision: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    decision_reason: Mapped[str | None] = mapped_column(String(160))
    person_id: Mapped[str | None] = mapped_column(ForeignKey("persons.id"), index=True)
    unknown_cluster_id: Mapped[str | None] = mapped_column(
        ForeignKey("unknown_clusters.id"), index=True
    )
    top_similarity: Mapped[float | None] = mapped_column(Float)
    second_similarity: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisSkip(Base):
    __tablename__ = "analysis_skips"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("skip"))
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    frame_index: Mapped[int | None] = mapped_column(Integer)
    detector_score: Mapped[float | None] = mapped_column(Float)
    quality_score: Mapped[float | None] = mapped_column(Float)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FacePrototype(Base):
    __tablename__ = "face_prototypes"
    __table_args__ = (UniqueConstraint("person_id", "source_track_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("proto"))
    person_id: Mapped[str] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    source_track_id: Mapped[str] = mapped_column(ForeignKey("face_tracks.id"), nullable=False)
    model_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    search_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    admitted_reason: Mapped[str] = mapped_column(String(64), default="manual_label")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UnknownCluster(Base):
    __tablename__ = "unknown_clusters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("U"))
    model_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    centroid: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    distinct_days: Mapped[int] = mapped_column(Integer, default=0)
    high_quality_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    labeled_person_id: Mapped[str | None] = mapped_column(ForeignKey("persons.id"))
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("unknown_clusters.id"))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    review_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UnknownClusterMember(Base):
    __tablename__ = "unknown_cluster_members"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("ucm"))
    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("unknown_clusters.id", ondelete="CASCADE"), index=True
    )
    track_id: Mapped[str] = mapped_column(
        ForeignKey("face_tracks.id", ondelete="CASCADE"), unique=True
    )
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    event_day: Mapped[str] = mapped_column(String(10), index=True)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CannotLink(Base):
    __tablename__ = "cannot_links"
    __table_args__ = (UniqueConstraint("left_track_id", "right_track_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cl"))
    left_track_id: Mapped[str] = mapped_column(ForeignKey("face_tracks.id"), index=True)
    right_track_id: Mapped[str] = mapped_column(ForeignKey("face_tracks.id"), index=True)
    reason: Mapped[str] = mapped_column(String(64), default="same_frame")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PersonObservation(Base):
    __tablename__ = "person_observations"
    __table_args__ = (UniqueConstraint("person_id", "event_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("obs"))
    person_id: Mapped[str] = mapped_column(
        ForeignKey("persons.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    event_day: Mapped[str] = mapped_column(String(10), index=True)
    source_track_id: Mapped[str] = mapped_column(ForeignKey("face_tracks.id"), nullable=False)
    similarity: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupReceipt(Base):
    __tablename__ = "backup_receipts"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("receipt")
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifact_manifest.id"), unique=True, index=True
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    remote_sha256: Mapped[str | None] = mapped_column(String(64))
    remote_size_bytes: Mapped[int | None] = mapped_column(Integer)
    remote_locator: Mapped[str | None] = mapped_column(Text)
    receipt_source: Mapped[str] = mapped_column(String(64), default="backup-specialist")
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DownloadReport(Base):
    __tablename__ = "download_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("dl"))
    event_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(96))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("msg"))
    topic: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    priority: Mapped[int] = mapped_column(Integer, default=50, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(
        String(24), default=OutboxState.PENDING.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManualOperation(Base):
    __tablename__ = "manual_operations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("op"))
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    before_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undo_operation_id: Mapped[str | None] = mapped_column(ForeignKey("manual_operations.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("audit")
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    subject_type: Mapped[str | None] = mapped_column(String(32))
    subject_id: Mapped[str | None] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(24), default="success")
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("sess"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    csrf_token_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class LoginThrottle(Base):
    __tablename__ = "login_throttle"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), default="system")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    model_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    detector_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recognizer_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="CPUExecutionProvider")
    license_class: Mapped[str] = mapped_column(
        String(64), default="non-commercial-research"
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index(
    "ix_ingest_claim",
    VideoIngest.state,
    VideoIngest.available_at,
    VideoIngest.lease_until,
)
Index(
    "ix_outbox_claim",
    OutboxMessage.state,
    OutboxMessage.available_at,
    OutboxMessage.priority,
)
Index("ix_events_timeline", Event.occurred_at, Event.analysis_state)
Index("ix_artifact_backup", ArtifactManifest.backup_state, ArtifactManifest.retention_class)
