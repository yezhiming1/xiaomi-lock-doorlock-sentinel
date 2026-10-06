from concurrent.futures import ThreadPoolExecutor
from threading import Event as ThreadEvent

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_people import _cluster_with_tracks

from doorlock_sentinel.api import create_app
from doorlock_sentinel.models import CannotLink, FaceTrack, ManualOperation, Person, VideoIngest
from doorlock_sentinel.people import (
    assign_cluster_to_person,
    cluster_person_conflicts,
    undo_operation,
)


def fixture(session, settings):
    person = Person(display_name="合成人物", relationship="neighbor")
    session.add(person)
    session.flush()
    cluster = _cluster_with_tracks(session, settings, count=1, start_index=900)
    source = session.query(FaceTrack).filter_by(unknown_cluster_id=cluster.id).one()
    target = FaceTrack(
        event_id=source.event_id,
        track_index=1,
        model_id=source.model_id,
        embedding=source.embedding,
        embedding_dimension=4,
        quality_score=0.9,
        person_id=person.id,
    )
    session.add(target)
    session.flush()
    left, right = sorted((source.id, target.id))
    conflict = CannotLink(left_track_id=left, right_track_id=right, reason="same_frame")
    session.add(conflict)
    session.flush()
    return cluster, person, conflict


def correction(conflict):
    return [{"id": conflict.id, "reason": "reflection"}]


def test_correction_is_exact_idempotent_audited_and_undo_restores_constraint(database, settings):
    with database.session() as session:
        cluster, person, conflict = fixture(session, settings)
        rows, revision = cluster_person_conflicts(session, cluster.id, person.id)
        assert [row.id for row in rows] == [conflict.id]
        kwargs = dict(
            cluster_id=cluster.id,
            target_person_id=person.id,
            idempotency_key="correct-reflection-0001",
            conflict_corrections=correction(conflict),
            review_revision=revision,
        )
        result = assign_cluster_to_person(session, settings, **kwargs)
        assert result["corrected_conflict_count"] == 1
        assert session.query(CannotLink).count() == 0
        assert assign_cluster_to_person(session, settings, **kwargs) == result
        with pytest.raises(ValueError, match="请求已变化"):
            assign_cluster_to_person(session, settings, **{**kwargs, "target_person_id": "other"})
        op = session.query(ManualOperation).one()
        assert op.before_json["corrected_constraints"][0]["correction_reason"] == "reflection"
        undo_operation(
            session, settings, operation_id=op.id, idempotency_key="undo-correction-0001"
        )
        session.flush()
        restored = session.query(CannotLink).one()
        assert restored.id == conflict.id
        assert restored.reason == "same_frame"
        assert cluster.labeled_person_id is None
        with pytest.raises(ValueError, match="同一画面"):
            assign_cluster_to_person(
                session,
                settings,
                cluster_id=cluster.id,
                target_person_id=person.id,
                idempotency_key="ordinary-retry-0001",
            )
        with pytest.raises(ValueError, match="操作已撤销"):
            assign_cluster_to_person(session, settings, **kwargs)


@pytest.mark.parametrize("mode", ["stale", "extra", "duplicate", "reason", "missing"])
def test_reject_invalid_corrections_without_deleting_constraint(database, settings, mode):
    with database.session() as session:
        cluster, person, conflict = fixture(session, settings)
        _, revision = cluster_person_conflicts(session, cluster.id, person.id)
        changes = correction(conflict)
        if mode == "stale":
            cluster.version += 1
        elif mode == "extra":
            changes.append({"id": "unrelated", "reason": "reflection"})
        elif mode == "duplicate":
            changes *= 2
        elif mode == "reason":
            changes[0]["reason"] = "force"
        else:
            changes = []
        with pytest.raises(ValueError):
            assign_cluster_to_person(
                session,
                settings,
                cluster_id=cluster.id,
                target_person_id=person.id,
                idempotency_key="invalid-correction-0001",
                conflict_corrections=changes,
                review_revision=revision,
            )
        assert session.query(CannotLink).count() == 1
        assert cluster.labeled_person_id is None
        assert session.query(ManualOperation).count() == 0


def test_post_failure_rolls_back_correction_and_assignment(database, settings, monkeypatch):
    with database.session() as session:
        cluster, person, conflict = fixture(session, settings)
        ids = cluster.id, person.id, conflict.id

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr("doorlock_sentinel.people.admit_prototype", fail)
    with pytest.raises(RuntimeError), database.session() as session:
        _, revision = cluster_person_conflicts(session, ids[0], ids[1])
        assign_cluster_to_person(
            session,
            settings,
            cluster_id=ids[0],
            target_person_id=ids[1],
            idempotency_key="rollback-correction-0001",
            conflict_corrections=[{"id": ids[2], "reason": "reflection"}],
            review_revision=revision,
        )
    with database.session() as session:
        assert session.get(CannotLink, ids[2]) is not None
        assert (
            session.query(FaceTrack).filter_by(unknown_cluster_id=ids[0], person_id=None).count()
            == 1
        )
        assert session.query(ManualOperation).count() == 0


def test_correction_preserves_unrelated_constraint(database, settings):
    with database.session() as session:
        cluster, person, conflict = fixture(session, settings)
        other = _cluster_with_tracks(session, settings, count=2, start_index=950)
        tracks = session.query(FaceTrack).filter_by(unknown_cluster_id=other.id).all()
        left, right = sorted(track.id for track in tracks)
        untouched = CannotLink(left_track_id=left, right_track_id=right, reason="same_frame")
        session.add(untouched)
        session.flush()
        _, revision = cluster_person_conflicts(session, cluster.id, person.id)
        assign_cluster_to_person(
            session,
            settings,
            cluster_id=cluster.id,
            target_person_id=person.id,
            idempotency_key="unrelated-correction-0001",
            conflict_corrections=correction(conflict),
            review_revision=revision,
        )
        assert session.get(CannotLink, untouched.id) is untouched


def test_target_change_invalidates_review(database, settings):
    with database.session() as session:
        cluster, person, conflict = fixture(session, settings)
        _, revision = cluster_person_conflicts(session, cluster.id, person.id)
        person.display_name = "修改后的合成人物"
        with pytest.raises(ValueError, match="冲突记录已变化"):
            assign_cluster_to_person(
                session,
                settings,
                cluster_id=cluster.id,
                target_person_id=person.id,
                idempotency_key="renamed-target-0001",
                conflict_corrections=correction(conflict),
                review_revision=revision,
            )
        assert session.get(CannotLink, conflict.id) is conflict


def test_review_authentication_csrf_and_real_api_request(database, settings):
    with database.session() as session:
        cluster, person, conflict = fixture(session, settings)
        cid, pid, conflict_id = cluster.id, person.id, conflict.id
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        url = f"/api/clusters/{cid}/person-conflicts?target_person_id={pid}"
        assert client.get(url).status_code == 401
        csrf = client.post(
            "/api/session/login", json={"password": "correct horse battery staple"}
        ).json()["csrf_token"]
        review = client.get(url)
        assert review.status_code == 200
        assert review.json()["items"][0]["id"] == conflict_id
        body = {
            "target_person_id": pid,
            "idempotency_key": "api-correction-0001",
            "conflict_corrections": [{"id": conflict_id, "reason": "reflection"}],
            "review_revision": review.json()["review_revision"],
        }
        assert client.post(f"/api/clusters/{cid}/assign-person", json=body).status_code == 403
        accepted = client.post(
            f"/api/clusters/{cid}/assign-person",
            json=body,
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["result"]["corrected_conflict_count"] == 1


def test_concurrent_api_corrections_reject_second_stale_request(database, settings, monkeypatch):
    import doorlock_sentinel.people as people

    with database.session() as session:
        cluster, person, conflict = fixture(session, settings)
        cid, pid, conflict_id = cluster.id, person.id, conflict.id
    original = people.cluster_person_conflicts
    first_read, release_first, second_started = ThreadEvent(), ThreadEvent(), ThreadEvent()
    call_count = 0

    def controlled_read(*args, **kwargs):
        nonlocal call_count
        result = original(*args, **kwargs)
        call_count += 1
        if call_count == 1:
            first_read.set()
            assert release_first.wait(10)
        return result

    with TestClient(create_app(settings), base_url="http://testserver") as client:
        csrf = client.post(
            "/api/session/login", json={"password": "correct horse battery staple"}
        ).json()["csrf_token"]
        review = client.get(f"/api/clusters/{cid}/person-conflicts?target_person_id={pid}").json()
        monkeypatch.setattr(people, "cluster_person_conflicts", controlled_read)
        body = {
            "target_person_id": pid,
            "conflict_corrections": [{"id": conflict_id, "reason": "reflection"}],
            "review_revision": review["review_revision"],
        }

        def submit(key):
            if key.endswith("second"):
                second_started.set()
            return client.post(
                f"/api/clusters/{cid}/assign-person",
                json={**body, "idempotency_key": key},
                headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(submit, "concurrent-first")
            assert first_read.wait(10)
            second = executor.submit(submit, "concurrent-second")
            assert second_started.wait(10)
            release_first.set()
            responses = [first.result(timeout=15), second.result(timeout=15)]
        assert sorted(r.status_code for r in responses) == [200, 409]
    with database.session() as session:
        assert (
            session.query(ManualOperation).filter_by(operation="assign_cluster_to_person").count()
            == 1
        )
        assert session.query(CannotLink).count() == 0
        assert session.get(type(cluster), cid).version == 2


def test_concurrent_people_merge_cannot_leave_correction_on_merged_person(
    database, settings, monkeypatch
):
    with database.session() as session:
        cluster, person, conflict = fixture(session, settings)
        destination = Person(display_name="另一个合成人物", relationship="neighbor")
        session.add(destination)
        session.flush()
        cid, pid, did, conflict_id = cluster.id, person.id, destination.id, conflict.id
    original = Session.scalars
    merge_read, release_merge, correction_started = ThreadEvent(), ThreadEvent(), ThreadEvent()

    def controlled_scalars(session, statement, *args, **kwargs):
        result = original(session, statement, *args, **kwargs)
        sql = str(statement)
        if (
            "FROM face_tracks" in sql
            and "face_tracks.person_id =" in sql
            and pid in statement.compile().params.values()
            and not merge_read.is_set()
        ):
            rows = list(result)
            merge_read.set()
            assert release_merge.wait(10)
            return iter(rows)
        return result

    with TestClient(create_app(settings), base_url="http://testserver") as client:
        csrf = client.post(
            "/api/session/login", json={"password": "correct horse battery staple"}
        ).json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf, "Origin": "http://testserver"}
        review = client.get(f"/api/clusters/{cid}/person-conflicts?target_person_id={pid}").json()
        monkeypatch.setattr(Session, "scalars", controlled_scalars)

        def merge():
            return client.post(
                "/api/people/merge",
                json={
                    "source_person_id": pid,
                    "target_person_id": did,
                    "idempotency_key": "cross-route-merge",
                },
                headers=headers,
            )

        def correct():
            correction_started.set()
            return client.post(
                f"/api/clusters/{cid}/assign-person",
                json={
                    "target_person_id": pid,
                    "idempotency_key": "cross-route-correct",
                    "review_revision": review["review_revision"],
                    "conflict_corrections": [{"id": conflict_id, "reason": "reflection"}],
                },
                headers=headers,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            merging = executor.submit(merge)
            assert merge_read.wait(10)
            correcting = executor.submit(correct)
            assert correction_started.wait(10)
            release_merge.set()
            assert merging.result(timeout=15).status_code == 200
            assert correcting.result(timeout=15).status_code == 409
    with database.session() as session:
        assert session.query(FaceTrack).filter_by(person_id=pid).count() == 0
        assert session.get(CannotLink, conflict_id) is not None
        assert session.get(type(cluster), cid).labeled_person_id is None


def test_ingest_retry_keeps_its_existing_independent_transaction(database, settings):
    with database.session() as session:
        ingest = VideoIngest(
            fingerprint="synthetic-retry",
            source_path="/synthetic/missing.mp4",
            original_name="synthetic.mp4",
            size_bytes=1,
            mtime_ns=1,
            state="dead",
        )
        session.add(ingest)
        session.flush()
        ingest_id = ingest.id
    client = TestClient(create_app(settings), base_url="http://testserver")
    try:
        csrf = client.post(
            "/api/session/login", json={"password": "correct horse battery staple"}
        ).json()["csrf_token"]
        response = client.post(
            f"/api/ingest/{ingest_id}/retry",
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
        )
        assert response.status_code == 200
        with database.session() as session:
            row = session.get(VideoIngest, ingest_id)
            assert row.state == "retry" and row.retry_requested
    finally:
        client.close()
