from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import stat
import threading
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import Database
from .models import (
    ArtifactManifest,
    ArtifactState,
    BackupReceipt,
    FaceTrack,
    utcnow,
)

logger = logging.getLogger(__name__)
_MANIFEST_LOCK = threading.Lock()

PERMANENT_RETENTION = {
    "identity_permanent",
    "high_quality_face_permanent",
    "model_permanent",
    "database_snapshot_permanent",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_like(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def safe_artifact_path(settings: Settings, path: Path) -> Path:
    """Resolve one regular artifact inside an approved runtime root without links."""
    candidate = path.absolute()
    try:
        metadata = candidate.stat(follow_symlinks=False)
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise ValueError("artifact path is unavailable or unsafe") from None
    for configured_root in (
        settings.inbox_dir,
        settings.data_dir,
        settings.models_dir,
    ):
        root = configured_root.absolute()
        try:
            resolved_root = root.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (FileNotFoundError, OSError, ValueError):
            continue
        try:
            if _is_link_like(root):
                raise ValueError("artifact root cannot be a symbolic link")
            current = candidate
            while True:
                if _is_link_like(current):
                    raise ValueError("artifact path cannot contain a symbolic link")
                if os.path.samefile(current, root):
                    break
                parent = current.parent
                if parent == current:
                    raise ValueError("artifact path is outside approved runtime roots")
                current = parent
        except (FileNotFoundError, OSError, ValueError):
            raise ValueError("artifact path is unavailable or unsafe") from None
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("artifact path is not a regular file")
        return resolved
    raise ValueError("artifact path is outside approved runtime roots")


def retention_due(
    settings: Settings,
    retention_class: str,
    created_at: datetime,
) -> datetime | None:
    if retention_class in PERMANENT_RETENTION:
        return None
    return created_at + timedelta(days=settings.normal_retention_days)


def register_artifact(
    session: Session,
    settings: Settings,
    *,
    path: Path,
    artifact_type: str,
    logical_path: str,
    retention_class: str,
    sha256: str | None = None,
    created_at: datetime | None = None,
) -> ArtifactManifest:
    path = safe_artifact_path(settings, path)
    existing = session.scalar(
        select(ArtifactManifest).where(ArtifactManifest.logical_path == logical_path)
    )
    stat = path.stat()
    digest = sha256 or file_sha256(path)
    created = created_at or utcnow()
    if existing:
        if existing.sha256 != digest or existing.size_bytes != stat.st_size:
            raise ValueError("artifact logical path already belongs to different bytes")
        return existing
    artifact = ArtifactManifest(
        artifact_type=artifact_type,
        logical_path=logical_path,
        local_path=str(path),
        size_bytes=stat.st_size,
        sha256=digest,
        state=ArtifactState.RECEIPT_PENDING.value,
        retention_class=retention_class,
        retention_due_at=retention_due(settings, retention_class, created),
        backup_state="pending",
        created_at=created,
    )
    session.add(artifact)
    session.flush()
    return artifact


def promote_artifact(session: Session, artifact_id: str | None) -> None:
    if not artifact_id:
        return
    artifact = session.get(ArtifactManifest, artifact_id)
    if not artifact:
        raise ValueError("artifact does not exist")
    artifact.retention_class = "high_quality_face_permanent"
    artifact.retention_due_at = None


def promote_track_artifacts(session: Session, track: FaceTrack) -> None:
    promote_artifact(session, track.best_face_artifact_id)


def apply_backup_receipt(
    session: Session,
    *,
    artifact_id: str,
    state: str,
    remote_sha256: str | None,
    remote_size_bytes: int | None,
    remote_locator: str | None,
    receipt_source: str,
    details: dict[str, Any] | None = None,
) -> BackupReceipt:
    artifact = session.get(ArtifactManifest, artifact_id)
    if not artifact:
        raise ValueError("artifact does not exist")
    normalized = state.strip().lower()
    if normalized not in {"verified", "failed", "missing"}:
        raise ValueError("unsupported receipt state")
    if normalized == "verified":
        if remote_sha256 != artifact.sha256:
            raise ValueError("receipt checksum does not match artifact")
        if remote_size_bytes != artifact.size_bytes:
            raise ValueError("receipt size does not match artifact")
    receipt = session.scalar(
        select(BackupReceipt).where(BackupReceipt.artifact_id == artifact.id)
    )
    if receipt is None:
        receipt = BackupReceipt(
            artifact_id=artifact.id,
            state=normalized,
            remote_sha256=remote_sha256,
            remote_size_bytes=remote_size_bytes,
            remote_locator=remote_locator,
            receipt_source=receipt_source,
            details_json=details or {},
        )
        session.add(receipt)
        session.flush()
    else:
        receipt.state = normalized
        receipt.remote_sha256 = remote_sha256
        receipt.remote_size_bytes = remote_size_bytes
        receipt.remote_locator = remote_locator
        receipt.receipt_source = receipt_source
        receipt.details_json = details or {}
    if normalized == "verified":
        receipt.verified_at = utcnow()
        artifact.state = ArtifactState.VERIFIED.value
        artifact.backup_state = "verified"
    else:
        receipt.verified_at = None
        artifact.backup_state = normalized
    artifact.backup_receipt_id = receipt.id
    return receipt


def _manifest_item(artifact: ArtifactManifest) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "artifact_type": artifact.artifact_type,
        "logical_path": artifact.logical_path,
        "local_path": artifact.local_path,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "state": artifact.state,
        "retention_class": artifact.retention_class,
        "retention_due_at": (
            artifact.retention_due_at.isoformat() if artifact.retention_due_at else None
        ),
        "backup_state": artifact.backup_state,
        "created_at": artifact.created_at.isoformat(),
    }


def export_manifest(database: Database, settings: Settings) -> Path:
    with _MANIFEST_LOCK:
        target = settings.export_dir / "artifact-manifest.json"
        temporary = target.with_suffix(".json.tmp")
        with database.session() as session:
            rows = list(
                session.scalars(
                    select(ArtifactManifest).order_by(
                        ArtifactManifest.created_at.asc(),
                        ArtifactManifest.id.asc(),
                    )
                )
            )
        payload = {
            "schema": 1,
            "generated_at": utcnow().isoformat(),
            "producer": "doorlock-sentinel",
            "artifacts": [_manifest_item(row) for row in rows],
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        return target


def create_database_snapshot(database: Database, settings: Settings) -> Path:
    source_name = database.engine.url.database
    if not source_name:
        raise RuntimeError("database snapshot requires a file-backed SQLite database")
    source = Path(source_name)
    if not source.is_file():
        raise FileNotFoundError(source)
    stamp = utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    target_dir = settings.export_dir / "database"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"doorlock-{stamp}.sqlite3"
    temporary = target.with_suffix(".sqlite3.tmp")
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(temporary) as target_connection,
    ):
        source_connection.backup(target_connection)
        row = target_connection.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise RuntimeError("database snapshot integrity check failed")
    os.chmod(temporary, 0o600)
    temporary.replace(target)
    with database.session() as session:
        register_artifact(
            session,
            settings,
            path=target,
            artifact_type="database_snapshot",
            logical_path=f"database/{target.name}",
            retention_class="database_snapshot_permanent",
        )
    export_manifest(database, settings)
    return target


class ManifestWorker:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def reconcile(self) -> dict[str, int]:
        changed = 0
        missing = 0
        with self.database.session() as session:
            rows = list(session.scalars(select(ArtifactManifest)))
            for artifact in rows:
                try:
                    safe_artifact_path(self.settings, Path(artifact.local_path))
                    exists = True
                except ValueError:
                    exists = False
                if not exists and artifact.state != ArtifactState.MISSING.value:
                    artifact.state = ArtifactState.MISSING.value
                    missing += 1
                    changed += 1
                elif exists and artifact.state == ArtifactState.MISSING.value:
                    artifact.state = (
                        ArtifactState.VERIFIED.value
                        if artifact.backup_state == "verified"
                        else ArtifactState.RECEIPT_PENDING.value
                    )
                    changed += 1
        export_manifest(self.database, self.settings)
        return {"changed": changed, "missing": missing}

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.reconcile)
            except Exception:
                logger.error(
                    "artifact manifest reconciliation failed code=MANIFEST_RECONCILE_FAILED"
                )
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=max(10, self.settings.manifest_export_seconds),
                )
