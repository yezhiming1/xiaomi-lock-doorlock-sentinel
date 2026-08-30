from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .artifacts import (
    apply_backup_receipt,
    create_database_snapshot,
    export_manifest,
    file_sha256,
)
from .download_status import StatusReport, record_status_report
from .outbox import acknowledge, claim_messages, reject
from .web_common import require_internal_token, runtime_from

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)


class WorkerRequest(BaseModel):
    worker: str = Field(min_length=1, max_length=128)


class NackRequest(WorkerRequest):
    error: str = Field(min_length=1, max_length=4000)
    retry_after_seconds: int = Field(default=30, ge=1, le=3600)


class BackupReceiptRequest(BaseModel):
    state: str = Field(min_length=1, max_length=24)
    remote_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    remote_size_bytes: int | None = Field(default=None, ge=0)
    remote_locator: str | None = Field(default=None, max_length=1000)
    receipt_source: str = Field(default="backup-specialist", min_length=1, max_length=64)
    details: dict[str, Any] = Field(default_factory=dict)


class DownloadReportRequest(BaseModel):
    event_digest: str = Field(min_length=16, max_length=64)
    event_time: datetime | None = None
    state: str = Field(pattern="^(discovered|retrying|downloaded|failed)$")
    attempts: int = Field(default=0, ge=0, le=100)
    error_code: str | None = Field(default=None, max_length=96)
    next_retry_at: datetime | None = None


@router.get("/outbox/claim")
def claim(
    request: Request,
    worker: str = Query(min_length=1, max_length=128),
    limit: int = Query(10, ge=1, le=50),
) -> dict[str, Any]:
    runtime = runtime_from(request)
    with runtime.database.session() as session:
        rows = claim_messages(session, runtime.settings, worker, limit)
        messages = [
            {
                "id": row.id,
                "topic": row.topic,
                "priority": row.priority,
                "payload": row.payload,
                "attempts": row.attempts,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    return {"messages": messages}


@router.post("/outbox/{message_id}/ack")
def ack(message_id: str, body: WorkerRequest, request: Request) -> dict[str, bool]:
    runtime = runtime_from(request)
    with runtime.database.session() as session:
        ok = acknowledge(session, message_id, body.worker)
    if not ok:
        raise HTTPException(status_code=409, detail="消息租约不属于该工作进程")
    return {"ok": True}


@router.post("/outbox/{message_id}/nack")
def nack(message_id: str, body: NackRequest, request: Request) -> dict[str, bool]:
    runtime = runtime_from(request)
    with runtime.database.session() as session:
        ok = reject(
            session,
            message_id,
            body.worker,
            body.error,
            body.retry_after_seconds,
        )
    if not ok:
        raise HTTPException(status_code=409, detail="消息租约不属于该工作进程")
    return {"ok": True}


@router.post("/download-reports")
def download_report(body: DownloadReportRequest, request: Request) -> dict[str, bool]:
    runtime = runtime_from(request)
    with runtime.database.session() as session:
        record_status_report(
            session,
            StatusReport(
                report_key=body.event_digest,
                recorded_at=body.event_time,
                state=body.state,
                attempts=body.attempts,
                error_code=body.error_code,
                next_retry_at=body.next_retry_at,
            ),
        )
    return {"ok": True}


@router.get("/artifacts/manifest")
def manifest(request: Request) -> FileResponse:
    runtime = runtime_from(request)
    path = export_manifest(runtime.database, runtime.settings)
    return FileResponse(path, media_type="application/json", filename=path.name)


@router.post("/artifacts/{artifact_id}/receipt")
def receipt(
    artifact_id: str,
    body: BackupReceiptRequest,
    request: Request,
) -> dict[str, Any]:
    runtime = runtime_from(request)
    try:
        with runtime.database.session() as session:
            row = apply_backup_receipt(
                session,
                artifact_id=artifact_id,
                state=body.state,
                remote_sha256=body.remote_sha256,
                remote_size_bytes=body.remote_size_bytes,
                remote_locator=body.remote_locator,
                receipt_source=body.receipt_source,
                details=body.details,
            )
            result = {"id": row.id, "state": row.state, "verified_at": row.verified_at}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    export_manifest(runtime.database, runtime.settings)
    return result


@router.post("/database/snapshot")
def database_snapshot(request: Request) -> dict[str, Any]:
    runtime = runtime_from(request)
    path = create_database_snapshot(runtime.database, runtime.settings)
    return {
        "ok": True,
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }
