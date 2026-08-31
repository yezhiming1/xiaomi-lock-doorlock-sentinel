from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .artifacts import export_manifest, file_sha256
from .config import Settings
from .db import Database
from .media_names import (
    CURRENT_VIDEO_RE,
    LEGACY_VIDEO_RE,
    build_legacy_video_mapping,
    derived_image_name,
)
from .models import ArtifactManifest, BackupReceipt, Event, FaceTrack, VideoIngest


@dataclass(frozen=True, slots=True)
class VideoMigration:
    ingest_id: str
    event_id: str
    source_artifact_id: str
    new_name: str
    new_path: Path


@dataclass(frozen=True, slots=True)
class ImageMigration:
    artifact_id: str
    old_path: Path
    new_path: Path


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    videos: tuple[VideoMigration, ...]
    images: tuple[ImageMigration, ...]

    def counts(self, status: str, dry_run: bool) -> dict[str, object]:
        return {
            "status": status,
            "dry_run": dry_run,
            "videos": len(self.videos),
            "face_images": sum("-a" in item.new_path.stem for item in self.images),
            "scene_images": sum("-b" in item.new_path.stem for item in self.images),
        }


def build_media_migration_plan(database: Database, settings: Settings) -> MigrationPlan:
    with database.session() as session:
        ingests = list(session.scalars(select(VideoIngest).order_by(VideoIngest.id)))
        names = [row.original_name for row in ingests]
        if any(
            not LEGACY_VIDEO_RE.fullmatch(name) and not CURRENT_VIDEO_RE.fullmatch(name)
            for name in names
        ):
            raise RuntimeError("MEDIA_MIGRATION_UNMANAGED_VIDEO_NAME")
        mapping = build_legacy_video_mapping(names)
        videos: list[VideoMigration] = []
        images: list[ImageMigration] = []
        for ingest in ingests:
            new_name = mapping.get(ingest.original_name)
            if new_name is None:
                continue
            new_path = settings.inbox_dir / new_name
            old_path = settings.inbox_dir / ingest.original_name
            if old_path.exists() or old_path.is_symlink():
                raise RuntimeError("MEDIA_MIGRATION_VIDEO_NOT_RENAMED")
            _verify_regular_artifact(new_path, ingest.size_bytes, ingest.sha256)
            event = session.scalar(
                select(Event).where(Event.video_ingest_id == ingest.id)
            )
            if event is None or not event.source_artifact_id:
                raise RuntimeError("MEDIA_MIGRATION_EVENT_MAPPING_MISSING")
            source = session.get(ArtifactManifest, event.source_artifact_id)
            if source is None or source.artifact_type != "source_video":
                raise RuntimeError("MEDIA_MIGRATION_SOURCE_ARTIFACT_MISSING")
            _require_unverified(session, source)
            videos.append(
                VideoMigration(
                    ingest_id=ingest.id,
                    event_id=event.id,
                    source_artifact_id=source.id,
                    new_name=new_name,
                    new_path=new_path,
                )
            )
            tracks = list(
                session.scalars(
                    select(FaceTrack)
                    .where(FaceTrack.event_id == event.id)
                    .order_by(FaceTrack.track_index)
                )
            )
            for track in tracks:
                for artifact_id, kind in (
                    (track.best_face_artifact_id, "face"),
                    (track.best_frame_artifact_id, "scene"),
                ):
                    artifact = session.get(ArtifactManifest, artifact_id)
                    if artifact is None or artifact.artifact_type not in {
                        "face_sample",
                        "scene_preview",
                    }:
                        raise RuntimeError("MEDIA_MIGRATION_DERIVED_ARTIFACT_MISSING")
                    _require_unverified(session, artifact)
                    old_image = Path(artifact.local_path)
                    new_image = old_image.with_name(
                        derived_image_name(new_name, track.track_index, kind)
                    )
                    _verify_derived_location(
                        settings,
                        old_image,
                        new_image,
                        artifact.size_bytes,
                        artifact.sha256,
                    )
                    images.append(
                        ImageMigration(
                            artifact_id=artifact.id,
                            old_path=old_image,
                            new_path=new_image,
                        )
                    )
    if len({item.new_path for item in images}) != len(images):
        raise RuntimeError("MEDIA_MIGRATION_IMAGE_COLLISION")
    return MigrationPlan(tuple(videos), tuple(images))


def migrate_media_names(
    database: Database,
    settings: Settings,
    *,
    apply: bool,
) -> dict[str, object]:
    with _migration_lock(settings):
        plan = build_media_migration_plan(database, settings)
        if not apply:
            return plan.counts("dry_run_ok", True)
        renamed = _rename_images(plan.images)
        committed = False
        try:
            with database.session() as session:
                for item in plan.videos:
                    ingest = session.get(VideoIngest, item.ingest_id)
                    event = session.get(Event, item.event_id)
                    source = session.get(ArtifactManifest, item.source_artifact_id)
                    if ingest is None or event is None or source is None:
                        raise RuntimeError("MEDIA_MIGRATION_DATABASE_DRIFT")
                    ingest.original_name = item.new_name
                    ingest.source_path = str(item.new_path.resolve())
                    ingest.fingerprint = _fingerprint(item.new_path)
                    source.local_path = str(item.new_path.resolve())
                    source.logical_path = str(
                        Path(source.logical_path).with_name(item.new_name)
                    ).replace("\\", "/")
                for item in plan.images:
                    artifact = session.get(ArtifactManifest, item.artifact_id)
                    if artifact is None:
                        raise RuntimeError("MEDIA_MIGRATION_DATABASE_DRIFT")
                    artifact.local_path = str(item.new_path.resolve())
                    artifact.logical_path = str(
                        Path(artifact.logical_path).with_name(item.new_path.name)
                    ).replace("\\", "/")
            committed = True
        finally:
            if not committed:
                _rollback_images(renamed)
        export_manifest(database, settings)
        _verify_completed(database, settings, plan)
        return plan.counts("migrated", False)


def _verify_regular_artifact(path: Path, size_bytes: int, digest: str | None) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        raise RuntimeError("MEDIA_MIGRATION_VIDEO_TARGET_MISSING") from None
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size != size_bytes:
        raise RuntimeError("MEDIA_MIGRATION_VIDEO_TARGET_INVALID")
    if digest and file_sha256(path) != digest:
        raise RuntimeError("MEDIA_MIGRATION_VIDEO_HASH_MISMATCH")


def _require_unverified(session: Session, artifact: ArtifactManifest) -> None:
    receipt_exists = session.scalar(
        select(BackupReceipt.id).where(BackupReceipt.artifact_id == artifact.id)
    )
    if (
        artifact.backup_state == "verified"
        or artifact.backup_receipt_id
        or receipt_exists
    ):
        raise RuntimeError("MEDIA_MIGRATION_BACKUP_RECEIPT_PRESENT")


def _verify_derived_location(
    settings: Settings,
    old_path: Path,
    new_path: Path,
    size_bytes: int,
    digest: str,
) -> None:
    try:
        old_path.resolve(strict=True).relative_to(settings.derived_dir.resolve(strict=True))
        new_path.parent.resolve(strict=True).relative_to(
            settings.derived_dir.resolve(strict=True)
        )
    except (FileNotFoundError, ValueError):
        raise RuntimeError("MEDIA_MIGRATION_DERIVED_PATH_INVALID") from None
    metadata = old_path.stat(follow_symlinks=False)
    if (
        old_path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size != size_bytes
        or file_sha256(old_path) != digest
    ):
        raise RuntimeError("MEDIA_MIGRATION_DERIVED_PATH_INVALID")
    if new_path.exists() or new_path.is_symlink():
        raise RuntimeError("MEDIA_MIGRATION_DERIVED_TARGET_EXISTS")


def _rename_images(items: tuple[ImageMigration, ...]) -> list[ImageMigration]:
    completed: list[ImageMigration] = []
    try:
        for item in items:
            os.link(item.old_path, item.new_path, follow_symlinks=False)
            if item.old_path.stat(follow_symlinks=False).st_ino != item.new_path.stat(
                follow_symlinks=False
            ).st_ino:
                raise RuntimeError("MEDIA_MIGRATION_IMAGE_LINK_MISMATCH")
            os.unlink(item.old_path)
            completed.append(item)
        _fsync_parents(item.new_path.parent for item in completed)
        return completed
    except Exception:
        _rollback_images(completed)
        raise


def _rollback_images(items: list[ImageMigration]) -> None:
    for item in reversed(items):
        if item.old_path.exists() and item.new_path.exists():
            if os.path.samefile(item.old_path, item.new_path):
                os.unlink(item.new_path)
                continue
            raise RuntimeError("MEDIA_MIGRATION_IMAGE_ROLLBACK_CONFLICT")
        if item.old_path.exists():
            continue
        if not item.new_path.exists() or item.new_path.is_symlink():
            raise RuntimeError("MEDIA_MIGRATION_IMAGE_ROLLBACK_MISSING")
        os.link(item.new_path, item.old_path, follow_symlinks=False)
        os.unlink(item.new_path)
    _fsync_parents(item.old_path.parent for item in items)


def _fsync_parents(paths: Iterator[Path]) -> None:
    if os.name == "nt":
        # Windows does not expose directory handles through os.open; the target
        # runtime is Linux, where each renamed directory is durably synced.
        return
    for path in sorted(set(paths)):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _fingerprint(path: Path) -> str:
    metadata = path.stat()
    payload = json.dumps(
        {
            "path": str(path.resolve()),
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
        },
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _verify_completed(
    database: Database,
    settings: Settings,
    plan: MigrationPlan,
) -> None:
    with database.session() as session:
        for item in plan.videos:
            ingest = session.get(VideoIngest, item.ingest_id)
            source = session.get(ArtifactManifest, item.source_artifact_id)
            if (
                ingest is None
                or source is None
                or ingest.original_name != item.new_name
                or Path(ingest.source_path) != item.new_path.resolve()
                or Path(source.local_path) != item.new_path.resolve()
            ):
                raise RuntimeError("MEDIA_MIGRATION_FINAL_VERIFY_FAILED")
        for item in plan.images:
            artifact = session.get(ArtifactManifest, item.artifact_id)
            if artifact is None or Path(artifact.local_path) != item.new_path.resolve():
                raise RuntimeError("MEDIA_MIGRATION_FINAL_VERIFY_FAILED")
            if not item.new_path.is_file() or item.old_path.exists():
                raise RuntimeError("MEDIA_MIGRATION_FINAL_VERIFY_FAILED")


@contextmanager
def _migration_lock(settings: Settings) -> Iterator[None]:
    path = settings.data_dir / ".media-name-migration.lock"
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        raise RuntimeError("MEDIA_MIGRATION_ALREADY_RUNNING") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeError("MEDIA_MIGRATION_LOCK_UNSAFE")
        yield
    finally:
        os.close(descriptor)
        with suppress(FileNotFoundError):
            path.unlink()
