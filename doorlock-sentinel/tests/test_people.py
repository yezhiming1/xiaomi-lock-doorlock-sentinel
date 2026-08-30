from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from conftest import create_event

from doorlock_sentinel.models import (
    CannotLink,
    FaceTrack,
    ManualOperation,
    Person,
    UnknownCluster,
    UnknownClusterMember,
)
from doorlock_sentinel.people import label_cluster, merge_people, undo_operation
from doorlock_sentinel.vector import pack_vector


def _cluster_with_tracks(session, settings, count: int = 3) -> UnknownCluster:
    vector = np.array([1, 0, 0, 0], dtype=np.float32)
    cluster = UnknownCluster(
        model_id=settings.model_id,
        centroid=pack_vector(vector),
        embedding_dimension=4,
        status="review_ready",
        first_seen=datetime(2026, 8, 27, tzinfo=timezone.utc),
        last_seen=datetime(2026, 8, 29, tzinfo=timezone.utc),
    )
    session.add(cluster)
    session.flush()
    for index in range(count):
        event = create_event(
            session,
            100 + index,
            datetime(2026, 8, 27, tzinfo=timezone.utc) + timedelta(days=index),
        )
        track = FaceTrack(
            event_id=event.id,
            track_index=0,
            model_id=settings.model_id,
            embedding=pack_vector(vector + np.array([0, index * 0.01, 0, 0])),
            embedding_dimension=4,
            quality_score=0.9 - index * 0.05,
            sample_count=3,
            unknown_cluster_id=cluster.id,
        )
        session.add(track)
        session.flush()
        session.add(
            UnknownClusterMember(
                cluster_id=cluster.id,
                track_id=track.id,
                event_id=event.id,
                event_day=event.occurred_at.date().isoformat(),
                similarity=0.99,
                quality_score=track.quality_score,
            )
        )
    cluster.member_count = count
    cluster.event_count = count
    cluster.distinct_days = count
    cluster.high_quality_count = count
    return cluster


def test_label_cluster_is_idempotent_and_undoable(database, settings):
    with database.session() as session:
        cluster = _cluster_with_tracks(session, settings)
        result = label_cluster(
            session,
            settings,
            cluster_id=cluster.id,
            display_name="快递员甲",
            relationship="courier",
            idempotency_key="label-cluster-0001",
        )
        replay = label_cluster(
            session,
            settings,
            cluster_id=cluster.id,
            display_name="不会覆盖",
            relationship="other",
            idempotency_key="label-cluster-0001",
        )
        assert replay == result
        person = session.get(Person, result["person_id"])
        assert person and person.relationship == "courier"
        operation_id = (
            session.query(ManualOperation)
            .filter_by(idempotency_key="label-cluster-0001")
            .one()
            .id
        )
        undone = undo_operation(
            session,
            settings,
            operation_id=operation_id,
            idempotency_key="undo-label-0001",
        )
        assert undone["status"] == "undone"
        assert cluster.status == "review_ready"


def test_people_seen_together_cannot_be_merged(database, settings):
    vector = np.array([1, 0, 0, 0], dtype=np.float32)
    with database.session() as session:
        event = create_event(session, 40)
        people = [Person(display_name=name, relationship="other") for name in ("甲", "乙")]
        session.add_all(people)
        session.flush()
        for index, person in enumerate(people):
            track = FaceTrack(
                    event_id=event.id,
                    track_index=index,
                    model_id=settings.model_id,
                    embedding=pack_vector(vector),
                    embedding_dimension=4,
                    quality_score=0.9,
                    person_id=person.id,
                )
            session.add(track)
            session.flush()
        tracks = list(session.query(FaceTrack).order_by(FaceTrack.track_index))
        left, right = sorted((tracks[0].id, tracks[1].id))
        session.add(
            CannotLink(
                left_track_id=left,
                right_track_id=right,
                reason="same_frame",
            )
        )
        session.flush()
        with pytest.raises(ValueError, match="同一画面"):
            merge_people(
                session,
                settings,
                source_person_id=people[0].id,
                target_person_id=people[1].id,
                idempotency_key="unsafe-merge-0001",
            )
