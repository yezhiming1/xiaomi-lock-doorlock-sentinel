from datetime import datetime, timezone

from doorlock_sentinel.metadata import EventMetadata
from doorlock_sentinel.risk import RiskScorer, TrackRiskInput


def test_unknown_failed_unlock_is_urgent(settings):
    scorer = RiskScorer(settings)
    metadata = EventMetadata(
        occurred_at=datetime(2026, 8, 29, 23, 30, tzinfo=timezone.utc),
        failed_unlock=True,
        touched_handle=True,
        dwell_seconds=70,
    )
    result = scorer.score(
        metadata,
        metadata.occurred_at,
        70,
        [TrackRiskInput(decision="unknown", cluster_event_count=2, quality_score=0.9)],
    )
    assert result.level == "urgent"
    assert result.score == 100
    assert any("失败开锁" in item for item in result.reasons)


def test_family_normal_event_is_record_only(settings):
    scorer = RiskScorer(settings)
    metadata = EventMetadata(
        occurred_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        dwell_seconds=4,
    )
    result = scorer.score(
        metadata,
        metadata.occurred_at,
        4,
        [TrackRiskInput(decision="known", relationship="family", quality_score=0.9)],
    )
    assert result.level == "record"
    assert result.score == 0
