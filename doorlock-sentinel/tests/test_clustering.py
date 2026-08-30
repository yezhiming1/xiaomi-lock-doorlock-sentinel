from datetime import datetime, timedelta, timezone

import numpy as np
from conftest import create_event

from doorlock_sentinel.models import CannotLink, FaceTrack
from doorlock_sentinel.recognition import UnknownClusterer
from doorlock_sentinel.vector import pack_vector


def add_track(session, event, index, vector, model_id="test-model-v1"):
    track = FaceTrack(
        event_id=event.id,
        track_index=index,
        model_id=model_id,
        embedding=pack_vector(vector),
        embedding_dimension=len(vector),
        quality_score=0.9,
        sample_count=3,
    )
    session.add(track)
    session.flush()
    return track


def test_cluster_becomes_review_ready_across_events_and_days(database, settings):
    clusterer = UnknownClusterer(settings)
    vector = np.array([1, 0, 0, 0], dtype=np.float32)
    cluster_id = None
    became_ready = False
    with database.session() as session:
        base = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
        for index, offset in enumerate([0, 1, 24]):
            event = create_event(session, index, base + timedelta(hours=offset))
            track = add_track(session, event, 0, vector)
            assignment = clusterer.assign(session, event, track, vector, 0.9)
            cluster_id = cluster_id or assignment.cluster.id
            assert assignment.cluster.id == cluster_id
            became_ready = became_ready or assignment.became_review_ready
        assert became_ready is True
        assert assignment.cluster.status == "review_ready"
        assert assignment.cluster.event_count == 3
        assert assignment.cluster.distinct_days == 2


def test_two_faces_in_same_event_are_never_forced_into_one_cluster(database, settings):
    clusterer = UnknownClusterer(settings)
    vector = np.array([1, 0, 0, 0], dtype=np.float32)
    with database.session() as session:
        event = create_event(session, 10)
        first = add_track(session, event, 0, vector)
        second = add_track(session, event, 1, vector)
        left, right = sorted((first.id, second.id))
        session.add(
            CannotLink(
                left_track_id=left,
                right_track_id=right,
                reason="same_frame",
            )
        )
        session.flush()
        one = clusterer.assign(session, event, first, vector, 0.9)
        two = clusterer.assign(session, event, second, vector, 0.9)
        assert one.cluster.id != two.cluster.id


def test_fragmented_tracks_without_same_frame_overlap_can_rejoin(database, settings):
    clusterer = UnknownClusterer(settings)
    vector = np.array([1, 0, 0, 0], dtype=np.float32)
    with database.session() as session:
        event = create_event(session, 11)
        first = add_track(session, event, 0, vector)
        second = add_track(session, event, 1, vector)
        one = clusterer.assign(session, event, first, vector, 0.9)
        two = clusterer.assign(session, event, second, vector, 0.9)
        assert one.cluster.id == two.cluster.id


def test_clusters_do_not_cross_model_versions(database, settings):
    clusterer = UnknownClusterer(settings)
    vector = np.array([1, 0, 0, 0], dtype=np.float32)
    with database.session() as session:
        event1 = create_event(session, 20)
        track1 = add_track(session, event1, 0, vector, "model-v1")
        cluster1 = clusterer.assign(session, event1, track1, vector, 0.9).cluster
        event2 = create_event(session, 21)
        track2 = add_track(session, event2, 0, vector, "model-v2")
        cluster2 = clusterer.assign(session, event2, track2, vector, 0.9).cluster
        assert cluster1.id != cluster2.id
        assert cluster1.model_id != cluster2.model_id
