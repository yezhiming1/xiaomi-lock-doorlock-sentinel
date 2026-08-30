from datetime import timedelta

from sqlalchemy import select

import doorlock_sentinel.pipeline as pipeline_module
from doorlock_sentinel.ingest import IngestWorker
from doorlock_sentinel.models import Event, VideoIngest, utcnow
from doorlock_sentinel.pipeline import AnalysisResult, ProcessingPipeline
from doorlock_sentinel.video import VideoInfo


def test_stale_processing_lease_is_recovered(database, settings):
    pipeline = ProcessingPipeline(settings, database)
    worker = IngestWorker(settings, database, pipeline)
    with database.session() as session:
        row = VideoIngest(
            fingerprint="stale",
            source_path="/tmp/stale.mp4",
            original_name="stale.mp4",
            size_bytes=1,
            mtime_ns=1,
            state="processing",
            lease_owner="dead-worker",
            lease_until=utcnow() - timedelta(minutes=1),
        )
        session.add(row)
    assert worker.recover_stale() == 1
    with database.session() as session:
        recovered = session.get(VideoIngest, row.id)
        assert recovered.state == "retry"
        assert recovered.lease_owner is None


def test_manifest_export_failure_after_commit_is_reconciled_later(
    database, settings, monkeypatch
):
    source = settings.inbox_dir / "xiaomi_lock_20260101T000000000Z_deadbeef.mp4"
    source.write_bytes(b"synthetic-placeholder")
    pipeline = ProcessingPipeline(settings, database)
    pipeline.analyze = lambda _path: AnalysisResult(
        info=VideoInfo(3.0, 640, 360, 15.0, 45),
        tracks=[],
        skips=[
            {
                "reason": "no_face_detected",
                "frame_index": None,
                "detector_score": None,
                "quality_score": None,
                "details": {"sampled_frames": 0},
            }
        ],
        sampled_frames=0,
    )

    def fail_export(*_args, **_kwargs):
        raise OSError("synthetic manifest failure")

    monkeypatch.setattr(pipeline_module, "export_manifest", fail_export)
    worker = IngestWorker(settings, database, pipeline)
    assert worker.discover() == 1
    ingest_id = worker.claim_one()
    assert ingest_id
    worker.process_one(ingest_id)
    with database.session() as session:
        ingest = session.get(VideoIngest, ingest_id)
        event = session.scalar(select(Event))
        assert ingest is not None and ingest.state == "processed"
        assert event is not None and event.analysis_state == "skipped"
    assert source.is_file()
