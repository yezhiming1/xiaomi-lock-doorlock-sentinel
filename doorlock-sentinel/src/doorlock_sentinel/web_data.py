from __future__ import annotations

import mimetypes
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .artifacts import safe_artifact_path
from .models import (
    ArtifactManifest,
    AuditLog,
    BackupReceipt,
    DownloadReport,
    Event,
    FaceTrack,
    ManualOperation,
    ModelRegistry,
    OutboxMessage,
    Person,
    RuntimeSetting,
    UnknownCluster,
    UnknownClusterMember,
    VideoIngest,
)
from .people import (
    label_cluster,
    mark_cluster_false_positive,
    merge_clusters,
    merge_people,
    rename_person,
    split_cluster,
    undo_operation,
)
from .web_common import AuthContext, authenticated, writable

router = APIRouter(prefix="/api", tags=["console"])


class IdempotentRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160)


class LabelClusterRequest(IdempotentRequest):
    display_name: str = Field(min_length=1, max_length=128)
    relationship: str = Field(min_length=1, max_length=32)


class RenamePersonRequest(LabelClusterRequest):
    pass


class MergePeopleRequest(IdempotentRequest):
    source_person_id: str = Field(min_length=1, max_length=64)
    target_person_id: str = Field(min_length=1, max_length=64)


class MergeClustersRequest(IdempotentRequest):
    source_cluster_id: str = Field(min_length=1, max_length=64)
    target_cluster_id: str = Field(min_length=1, max_length=64)


class SplitClusterRequest(IdempotentRequest):
    track_ids: list[str] = Field(min_length=1, max_length=100)


class NotificationSettingsRequest(BaseModel):
    identity_notifications_enabled: bool
    risk_notifications_enabled: bool


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _artifact_url(artifact_id: str | None) -> str | None:
    return f"/api/artifacts/{artifact_id}" if artifact_id else None


def _track_item(context: AuthContext, track: FaceTrack) -> dict[str, Any]:
    person = (
        context.database_session.get(Person, track.person_id)
        if track.person_id
        else None
    )
    cluster = (
        context.database_session.get(UnknownCluster, track.unknown_cluster_id)
        if track.unknown_cluster_id
        else None
    )
    return {
        "id": track.id,
        "track_index": track.track_index,
        "decision": track.decision,
        "decision_reason": track.decision_reason,
        "quality_score": round(track.quality_score, 4),
        "sample_count": track.sample_count,
        "representative": track.representative,
        "person": (
            {
                "id": person.id,
                "display_name": person.display_name,
                "relationship": person.relationship,
            }
            if person
            else None
        ),
        "cluster_id": cluster.id if cluster else None,
        "similarity": track.top_similarity,
        "face_url": _artifact_url(track.best_face_artifact_id),
        "preview_url": _artifact_url(track.best_frame_artifact_id),
    }


def _event_item(context: AuthContext, event: Event, *, detail: bool = False) -> dict[str, Any]:
    tracks = list(
        context.database_session.scalars(
            select(FaceTrack)
            .where(FaceTrack.event_id == event.id)
            .order_by(FaceTrack.track_index.asc())
        )
    )
    preview_id = next(
        (track.best_frame_artifact_id for track in tracks if track.best_frame_artifact_id),
        None,
    )
    item: dict[str, Any] = {
        "id": event.id,
        "occurred_at": _iso(event.occurred_at),
        "downloaded_at": _iso(event.downloaded_at),
        "event_type": event.event_type,
        "unlock_method": event.unlock_method,
        "operation_user": event.operation_user,
        "duration_seconds": round(event.duration_seconds, 2),
        "analysis_state": event.analysis_state,
        "track_count": event.track_count,
        "skipped_face_count": event.skipped_face_count,
        "risk_score": event.risk_score,
        "risk_level": event.risk_level,
        "risk_reasons": event.risk_reasons,
        "preview_url": _artifact_url(preview_id),
        "video_url": _artifact_url(event.source_artifact_id),
        "tracks": [_track_item(context, track) for track in tracks],
    }
    if detail:
        item["metadata"] = event.metadata_json
    return item


def _notification_settings(context: AuthContext) -> dict[str, bool]:
    rows = {
        row.key: bool(row.value_json)
        for row in context.database_session.scalars(
            select(RuntimeSetting).where(
                RuntimeSetting.key.in_(
                    ["identity_notifications_enabled", "risk_notifications_enabled"]
                )
            )
        )
    }
    return {
        "identity_notifications_enabled": rows.get(
            "identity_notifications_enabled",
            context.runtime.settings.identity_notifications_enabled,
        ),
        "risk_notifications_enabled": rows.get(
            "risk_notifications_enabled",
            context.runtime.settings.risk_notifications_enabled,
        ),
        "failure_notifications_enabled": True,
    }


@router.get("/bootstrap")
def bootstrap(
    context: Annotated[AuthContext, Depends(authenticated)],
) -> dict[str, Any]:
    session = context.database_session
    recent = list(
        session.scalars(select(Event).order_by(Event.occurred_at.desc()).limit(8))
    )
    def scalar_count(model, *where) -> int:
        return int(
            session.scalar(select(func.count()).select_from(model).where(*where))
            or 0
        )
    latest_receipt = session.scalar(
        select(BackupReceipt)
        .where(BackupReceipt.state == "verified")
        .order_by(BackupReceipt.verified_at.desc())
        .limit(1)
    )
    return {
        "version": "0.0.1",
        "counts": {
            "events": scalar_count(Event),
            "people": scalar_count(Person, Person.status != "merged"),
            "review_clusters": scalar_count(
                UnknownCluster,
                UnknownCluster.status.in_(["candidate", "review_ready"]),
            ),
            "failed_analysis": scalar_count(VideoIngest, VideoIngest.state == "dead"),
            "backup_pending": scalar_count(
                ArtifactManifest, ArtifactManifest.backup_state != "verified"
            ),
        },
        "recent_events": [_event_item(context, event) for event in recent],
        "notifications": _notification_settings(context),
        "backup": {
            "last_verified_at": _iso(latest_receipt.verified_at) if latest_receipt else None,
            "owner": "115-backup-specialist",
        },
        "analysis": {
            "ready": context.runtime.pipeline.ready,
            "error": context.runtime.pipeline.readiness_error,
            "model_id": context.runtime.settings.model_id,
        },
    }


@router.get("/events")
def events(
    context: Annotated[AuthContext, Depends(authenticated)],
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    state: str | None = Query(None, max_length=32),
) -> dict[str, Any]:
    query = select(Event)
    count_query = select(func.count()).select_from(Event)
    if state:
        query = query.where(Event.analysis_state == state)
        count_query = count_query.where(Event.analysis_state == state)
    rows = list(
        context.database_session.scalars(
            query.order_by(Event.occurred_at.desc()).offset(offset).limit(limit)
        )
    )
    return {
        "total": int(context.database_session.scalar(count_query) or 0),
        "items": [_event_item(context, event) for event in rows],
    }


@router.get("/events/{event_id}")
def event_detail(
    event_id: str,
    context: Annotated[AuthContext, Depends(authenticated)],
) -> dict[str, Any]:
    event = context.database_session.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="录像记录不存在")
    return _event_item(context, event, detail=True)


@router.get("/artifacts/{artifact_id}")
def artifact(
    artifact_id: str,
    context: Annotated[AuthContext, Depends(authenticated)],
) -> FileResponse:
    row = context.database_session.get(ArtifactManifest, artifact_id)
    if not row:
        raise HTTPException(status_code=404, detail="文件记录不存在")
    try:
        path = safe_artifact_path(context.runtime.settings, Path(row.local_path))
    except ValueError:
        raise HTTPException(
            status_code=410, detail="本地文件已不再保留"
        ) from None
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        content_disposition_type="inline",
    )


@router.get("/people")
def people(
    context: Annotated[AuthContext, Depends(authenticated)],
) -> dict[str, Any]:
    rows = list(
        context.database_session.scalars(
            select(Person)
            .where(Person.status != "merged")
            .order_by(Person.last_seen.desc().nullslast(), Person.created_at.desc())
        )
    )
    items = []
    for person in rows:
        exemplar = context.database_session.scalar(
            select(FaceTrack)
            .where(FaceTrack.person_id == person.id)
            .order_by(FaceTrack.representative.desc(), FaceTrack.quality_score.desc())
            .limit(1)
        )
        items.append(
            {
                "id": person.id,
                "display_name": person.display_name,
                "relationship": person.relationship,
                "status": person.status,
                "matched_events": person.matched_events,
                "distinct_days": person.distinct_days,
                "first_seen": _iso(person.first_seen),
                "last_seen": _iso(person.last_seen),
                "face_url": _artifact_url(exemplar.best_face_artifact_id) if exemplar else None,
            }
        )
    return {"items": items}


@router.get("/clusters")
def clusters(
    context: Annotated[AuthContext, Depends(authenticated)],
) -> dict[str, Any]:
    rows = list(
        context.database_session.scalars(
            select(UnknownCluster)
            .where(UnknownCluster.status.in_(["candidate", "review_ready"]))
            .order_by(UnknownCluster.last_seen.desc())
        )
    )
    items = []
    for cluster in rows:
        members = list(
            context.database_session.execute(
                select(UnknownClusterMember, FaceTrack)
                .join(FaceTrack, UnknownClusterMember.track_id == FaceTrack.id)
                .where(UnknownClusterMember.cluster_id == cluster.id)
                .order_by(FaceTrack.quality_score.desc())
            ).all()
        )
        items.append(
            {
                "id": cluster.id,
                "status": cluster.status,
                "member_count": cluster.member_count,
                "event_count": cluster.event_count,
                "distinct_days": cluster.distinct_days,
                "high_quality_count": cluster.high_quality_count,
                "first_seen": _iso(cluster.first_seen),
                "last_seen": _iso(cluster.last_seen),
                "tracks": [
                    {
                        "id": track.id,
                        "event_id": member.event_id,
                        "quality_score": round(track.quality_score, 4),
                        "face_url": _artifact_url(track.best_face_artifact_id),
                        "preview_url": _artifact_url(track.best_frame_artifact_id),
                    }
                    for member, track in members
                ],
            }
        )
    return {"items": items}


@router.get("/operations")
def operations(
    context: Annotated[AuthContext, Depends(authenticated)],
    limit: int = Query(100, ge=1, le=200),
) -> dict[str, Any]:
    rows = list(
        context.database_session.scalars(
            select(ManualOperation).order_by(ManualOperation.created_at.desc()).limit(limit)
        )
    )
    return {
        "items": [
            {
                "id": row.id,
                "operation": row.operation,
                "subject_type": row.subject_type,
                "subject_id": row.subject_id,
                "after": row.after_json,
                "created_at": _iso(row.created_at),
                "undone_at": _iso(row.undone_at),
            }
            for row in rows
        ]
    }


@router.get("/system")
def system(
    context: Annotated[AuthContext, Depends(authenticated)],
) -> dict[str, Any]:
    session = context.database_session
    disk = shutil.disk_usage(context.runtime.settings.data_dir)
    model = session.get(ModelRegistry, context.runtime.settings.model_id)
    audits = list(
        session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(40))
    )
    downloads = list(
        session.scalars(
            select(DownloadReport).order_by(DownloadReport.updated_at.desc()).limit(30)
        )
    )
    failed_ingests = list(
        session.scalars(
            select(VideoIngest)
            .where(VideoIngest.state == "dead")
            .order_by(VideoIngest.updated_at.desc())
            .limit(50)
        )
    )
    outbox_counts = dict(
        session.execute(
            select(OutboxMessage.state, func.count()).group_by(OutboxMessage.state)
        ).all()
    )
    backup_counts = dict(
        session.execute(
            select(ArtifactManifest.backup_state, func.count()).group_by(
                ArtifactManifest.backup_state
            )
        ).all()
    )
    return {
        "service": {
            "version": "0.0.1",
            "analysis_ready": context.runtime.pipeline.ready,
            "analysis_error": context.runtime.pipeline.readiness_error,
            "database": "ready",
        },
        "storage": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
        },
        "model": (
            {
                "model_id": model.model_id,
                "provider": model.provider,
                "license_class": model.license_class,
                "active": model.active,
            }
            if model
            else {"model_id": context.runtime.settings.model_id, "active": False}
        ),
        "backup_counts": backup_counts,
        "outbox_counts": outbox_counts,
        "download_reports": [
            {
                "event_digest": row.event_digest,
                "event_time": _iso(row.event_time),
                "state": row.state,
                "attempts": row.attempts,
                "error_code": row.error_code,
                "next_retry_at": _iso(row.next_retry_at),
            }
            for row in downloads
        ],
        "failed_ingests": [
            {
                "id": row.id,
                "file_name": row.original_name,
                "attempts": row.attempts,
                "error_code": row.last_error_code,
                "error": row.last_error,
                "updated_at": _iso(row.updated_at),
            }
            for row in failed_ingests
        ],
        "audits": [
            {
                "action": row.action,
                "subject_type": row.subject_type,
                "subject_id": row.subject_id,
                "outcome": row.outcome,
                "created_at": _iso(row.created_at),
            }
            for row in audits
        ],
        "notifications": _notification_settings(context),
    }


def _mutate(
    context: AuthContext,
    action: str,
    subject_type: str,
    subject_id: str | None,
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    try:
        result = operation()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    context.security.audit(
        context.database_session,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
    )
    return {"ok": True, "result": result}


@router.post("/clusters/{cluster_id}/label")
def cluster_label(
    cluster_id: str,
    body: LabelClusterRequest,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, Any]:
    return _mutate(
        context,
        "cluster.label",
        "cluster",
        cluster_id,
        lambda: label_cluster(
            context.database_session,
            context.runtime.settings,
            cluster_id=cluster_id,
            display_name=body.display_name,
            relationship=body.relationship,
            idempotency_key=body.idempotency_key,
        ),
    )


@router.post("/people/{person_id}/rename")
def person_rename(
    person_id: str,
    body: RenamePersonRequest,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, Any]:
    return _mutate(
        context,
        "person.rename",
        "person",
        person_id,
        lambda: rename_person(
            context.database_session,
            person_id=person_id,
            display_name=body.display_name,
            relationship=body.relationship,
            idempotency_key=body.idempotency_key,
        ),
    )


@router.post("/people/merge")
def people_merge(
    body: MergePeopleRequest,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, Any]:
    return _mutate(
        context,
        "people.merge",
        "person",
        body.source_person_id,
        lambda: merge_people(
            context.database_session,
            context.runtime.settings,
            source_person_id=body.source_person_id,
            target_person_id=body.target_person_id,
            idempotency_key=body.idempotency_key,
        ),
    )


@router.post("/clusters/merge")
def clusters_merge(
    body: MergeClustersRequest,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, Any]:
    return _mutate(
        context,
        "clusters.merge",
        "cluster",
        body.source_cluster_id,
        lambda: merge_clusters(
            context.database_session,
            context.runtime.settings,
            source_cluster_id=body.source_cluster_id,
            target_cluster_id=body.target_cluster_id,
            idempotency_key=body.idempotency_key,
        ),
    )


@router.post("/clusters/{cluster_id}/split")
def cluster_split(
    cluster_id: str,
    body: SplitClusterRequest,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, Any]:
    return _mutate(
        context,
        "cluster.split",
        "cluster",
        cluster_id,
        lambda: split_cluster(
            context.database_session,
            context.runtime.settings,
            cluster_id=cluster_id,
            track_ids=body.track_ids,
            idempotency_key=body.idempotency_key,
        ),
    )


@router.post("/clusters/{cluster_id}/false-positive")
def cluster_false_positive(
    cluster_id: str,
    body: IdempotentRequest,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, Any]:
    return _mutate(
        context,
        "cluster.false_positive",
        "cluster",
        cluster_id,
        lambda: mark_cluster_false_positive(
            context.database_session,
            cluster_id=cluster_id,
            idempotency_key=body.idempotency_key,
        ),
    )


@router.post("/operations/{operation_id}/undo")
def operation_undo(
    operation_id: str,
    body: IdempotentRequest,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, Any]:
    return _mutate(
        context,
        "operation.undo",
        "operation",
        operation_id,
        lambda: undo_operation(
            context.database_session,
            context.runtime.settings,
            operation_id=operation_id,
            idempotency_key=body.idempotency_key,
        ),
    )


@router.post("/ingest/{ingest_id}/retry")
def retry_ingest(
    ingest_id: str,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, bool]:
    if not context.runtime.ingest.request_retry(ingest_id):
        raise HTTPException(status_code=409, detail="该录像当前不可重试")
    context.security.audit(
        context.database_session,
        action="analysis.retry",
        subject_type="ingest",
        subject_id=ingest_id,
    )
    return {"ok": True}


@router.put("/settings/notifications")
def notification_settings(
    body: NotificationSettingsRequest,
    context: Annotated[AuthContext, Depends(writable)],
) -> dict[str, Any]:
    for key, value in body.model_dump().items():
        row = context.database_session.get(RuntimeSetting, key)
        if row is None:
            row = RuntimeSetting(key=key, value_json=value, updated_by="owner")
            context.database_session.add(row)
        else:
            row.value_json = value
            row.updated_by = "owner"
    context.security.audit(
        context.database_session,
        action="settings.notifications",
        details=body.model_dump(),
    )
    return {
        "ok": True,
        "notifications": {
            **body.model_dump(),
            "failure_notifications_enabled": True,
        },
    }
