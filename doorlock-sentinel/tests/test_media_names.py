from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from doorlock_sentinel.artifacts import file_sha256
from doorlock_sentinel.media_migration import migrate_media_names
from doorlock_sentinel.media_names import (
    build_legacy_video_mapping,
    derived_image_name,
    occurred_at_from_filename,
)
from doorlock_sentinel.models import (
    ArtifactManifest,
    Event,
    FaceTrack,
    VideoIngest,
)


def test_beijing_video_and_derived_names_are_stable():
    legacy = [
        "xiaomi_lock_20260828T000000000Z_aaaaaaaaaaaa.mp4",
        "xiaomi_lock_20260828T000000500Z_bbbbbbbbbbbb.mp4",
    ]
    mapping = build_legacy_video_mapping(legacy)
    assert mapping[legacy[0]] == "xiaomi_lock_20260828T080000.mp4"
    assert mapping[legacy[1]] == "xiaomi_lock_20260828T080000-02.mp4"
    assert (
        occurred_at_from_filename(Path(mapping[legacy[0]]))
        == datetime(2026, 8, 28, tzinfo=timezone.utc)
    )
    assert derived_image_name(mapping[legacy[0]], 0, "face").endswith("-a001.jpg")
    assert derived_image_name(mapping[legacy[0]], 0, "scene").endswith("-b001.jpg")


def test_media_migration_updates_files_database_and_manifest(database, settings):
    old_name = "xiaomi_lock_20260828T000000000Z_aaaaaaaaaaaa.mp4"
    new_name = "xiaomi_lock_20260828T080000.mp4"
    new_video = settings.inbox_dir / new_name
    new_video.write_bytes(b"synthetic-video")
    video_digest = file_sha256(new_video)
    derived = settings.derived_dir / "2026" / "08" / "28"
    derived.mkdir(parents=True)
    old_face = derived / "evt_fixture_track00_face.jpg"
    old_scene = derived / "evt_fixture_track00_scene.jpg"
    old_face.write_bytes(b"synthetic-face")
    old_scene.write_bytes(b"synthetic-scene")

    with database.session() as session:
        ingest = VideoIngest(
            fingerprint="legacy-fingerprint",
            source_path=str(settings.inbox_dir / old_name),
            original_name=old_name,
            size_bytes=new_video.stat().st_size,
            mtime_ns=new_video.stat().st_mtime_ns,
            sha256=video_digest,
            state="processed",
        )
        session.add(ingest)
        session.flush()
        source = ArtifactManifest(
            artifact_type="source_video",
            logical_path=f"events/2026/08/28/{old_name}",
            local_path=str(settings.inbox_dir / old_name),
            size_bytes=new_video.stat().st_size,
            sha256=video_digest,
            state="receipt_pending",
            retention_class="ordinary_35d",
            backup_state="pending",
        )
        face = ArtifactManifest(
            artifact_type="face_sample",
            logical_path=f"derived/2026/08/28/{old_face.name}",
            local_path=str(old_face),
            size_bytes=old_face.stat().st_size,
            sha256=file_sha256(old_face),
            state="receipt_pending",
            retention_class="ordinary_35d",
            backup_state="pending",
        )
        scene = ArtifactManifest(
            artifact_type="scene_preview",
            logical_path=f"derived/2026/08/28/{old_scene.name}",
            local_path=str(old_scene),
            size_bytes=old_scene.stat().st_size,
            sha256=file_sha256(old_scene),
            state="receipt_pending",
            retention_class="ordinary_35d",
            backup_state="pending",
        )
        session.add_all([source, face, scene])
        session.flush()
        event = Event(
            video_ingest_id=ingest.id,
            source_artifact_id=source.id,
            occurred_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            duration_seconds=5,
        )
        session.add(event)
        session.flush()
        ingest.event_id = event.id
        session.add(
            FaceTrack(
                event_id=event.id,
                track_index=0,
                model_id="fixture-model",
                embedding=b"\0" * 16,
                embedding_dimension=4,
                quality_score=0.8,
                best_face_artifact_id=face.id,
                best_frame_artifact_id=scene.id,
            )
        )

    assert migrate_media_names(database, settings, apply=False) == {
        "status": "dry_run_ok",
        "dry_run": True,
        "videos": 1,
        "face_images": 1,
        "scene_images": 1,
    }
    assert migrate_media_names(database, settings, apply=True)["status"] == "migrated"
    new_face = derived / "xiaomi_lock_20260828T080000-a001.jpg"
    new_scene = derived / "xiaomi_lock_20260828T080000-b001.jpg"
    assert new_face.is_file() and new_scene.is_file()
    assert not old_face.exists() and not old_scene.exists()
    with database.session() as session:
        ingest = session.scalar(select(VideoIngest))
        artifacts = list(session.scalars(select(ArtifactManifest)))
        assert ingest is not None and ingest.original_name == new_name
        assert Path(ingest.source_path) == new_video.resolve()
        assert {Path(row.local_path).name for row in artifacts} == {
            new_name,
            new_face.name,
            new_scene.name,
        }
    assert (settings.export_dir / "artifact-manifest.json").is_file()
    assert migrate_media_names(database, settings, apply=False)["videos"] == 0
