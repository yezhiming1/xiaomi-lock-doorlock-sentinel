import tempfile
from pathlib import Path

import pytest

from doorlock_sentinel.artifacts import (
    apply_backup_receipt,
    export_manifest,
    file_sha256,
    register_artifact,
    safe_artifact_path,
)


def test_backup_receipt_requires_exact_remote_identity(database, settings):
    source = settings.data_dir / "sample.mp4"
    source.write_bytes(b"private-video-placeholder")
    with database.session() as session:
        artifact = register_artifact(
            session,
            settings,
            path=source,
            artifact_type="source_video",
            logical_path="events/2026/08/29/sample.mp4",
            retention_class="ordinary_35d",
        )
        with pytest.raises(ValueError, match="checksum"):
            apply_backup_receipt(
                session,
                artifact_id=artifact.id,
                state="verified",
                remote_sha256="0" * 64,
                remote_size_bytes=source.stat().st_size,
                remote_locator="remote/object",
                receipt_source="test",
            )
        receipt = apply_backup_receipt(
            session,
            artifact_id=artifact.id,
            state="verified",
            remote_sha256=file_sha256(source),
            remote_size_bytes=source.stat().st_size,
            remote_locator="remote/object",
            receipt_source="test",
        )
        assert receipt.state == "verified"
    manifest = export_manifest(database, settings)
    assert Path(manifest).is_file()
    assert source.is_file(), "recognition must never delete source media after backup"


def test_artifact_paths_cannot_escape_runtime_roots(settings, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="outside approved runtime roots"):
        safe_artifact_path(settings, outside)

    source = settings.inbox_dir / "inside.mp4"
    source.write_bytes(b"inside")
    assert safe_artifact_path(settings, source) == source.resolve()


def test_artifact_path_accepts_canonical_name_for_configured_alias(settings):
    with tempfile.TemporaryDirectory(prefix="doorlock-artifact-alias-") as directory:
        alias_root = Path(directory) / "inbox"
        alias_root.mkdir()
        source = alias_root / "inside.mp4"
        source.write_bytes(b"inside")
        alias_settings = settings.model_copy(update={"inbox_dir": alias_root})

        assert safe_artifact_path(alias_settings, source.resolve()) == source.resolve()
