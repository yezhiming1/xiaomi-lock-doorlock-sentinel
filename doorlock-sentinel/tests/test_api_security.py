import hashlib

import numpy as np
from conftest import create_event
from fastapi.testclient import TestClient

from doorlock_sentinel.api import create_app
from doorlock_sentinel.db import Database
from doorlock_sentinel.models import (
    ArtifactManifest,
    Base,
    FaceTrack,
    LoginThrottle,
    Person,
    UnknownCluster,
    UnknownClusterMember,
)
from doorlock_sentinel.vector import pack_vector
from doorlock_sentinel.web_data import OPERATION_LABELS, LabelClusterRequest


def _migrate_for_test(settings):
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    database.engine.dispose()


def test_password_session_csrf_and_internal_boundary(settings):
    _migrate_for_test(settings)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/api/session")
        assert response.json() == {"authenticated": False}
        rejected = client.post("/api/session/login", json={"password": "wrong"})
        assert rejected.status_code == 401
        with app.state.runtime.database.session() as session:
            throttle = session.query(LoginThrottle).one()
            assert throttle.failed_count == 1

        accepted = client.post(
            "/api/session/login",
            json={"password": "correct horse battery staple"},
        )
        assert accepted.status_code == 200
        csrf = accepted.json()["csrf_token"]
        assert "doorlock_session=" in accepted.headers["set-cookie"]
        assert "HttpOnly" in accepted.headers["set-cookie"]
        assert "SameSite=strict" in accepted.headers["set-cookie"]
        assert client.get("/api/bootstrap").status_code == 200
        assert (
            client.put(
                "/api/settings/notifications",
                json={
                    "identity_notifications_enabled": False,
                    "risk_notifications_enabled": False,
                },
            ).status_code
            == 403
        )
        updated = client.put(
            "/api/settings/notifications",
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
            json={
                "identity_notifications_enabled": True,
                "risk_notifications_enabled": False,
            },
        )
        assert updated.status_code == 200
        assert client.get("/internal/outbox/claim?worker=test").status_code == 401
        assert (
            client.get(
                "/internal/outbox/claim?worker=test",
                headers={"X-Internal-Token": "test-internal-secret"},
            ).status_code
            == 200
        )


def test_security_headers_cover_static_console(settings):
    _migrate_for_test(settings)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"
        assert "default-src 'self'" in response.headers["content-security-policy"]
        app_js = client.get("/app.js")
        assert app_js.headers["cache-control"] == "no-store"
        assert 'self: "我"' in app_js.text
        assert 'friend: "朋友"' in app_js.text


def test_label_request_allows_omitted_or_blank_name():
    omitted = LabelClusterRequest(
        relationship="neighbor",
        idempotency_key="request-auto-name-0001",
    )
    blank = LabelClusterRequest(
        display_name="",
        relationship="courier",
        idempotency_key="request-auto-name-0002",
    )
    assert omitted.display_name == ""
    assert blank.display_name == ""


def test_supported_manual_operations_have_chinese_labels():
    assert OPERATION_LABELS == {
        "label_cluster": "确认人物",
        "rename_person": "修改人物",
        "assign_cluster_to_person": "并入已确认人物",
        "merge_people": "合并已确认人物",
        "merge_clusters": "合并待确认人物",
        "split_cluster": "拆分待确认人物",
        "cluster_false_positive": "标记误检",
        "undo": "撤销操作",
    }


def test_cluster_can_be_assigned_to_person_and_operation_is_localized(settings):
    _migrate_for_test(settings)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        accepted = client.post(
            "/api/session/login",
            json={"password": "correct horse battery staple"},
        )
        csrf = accepted.json()["csrf_token"]
        with app.state.runtime.database.session() as session:
            person = Person(display_name="已确认人物甲", relationship="neighbor")
            session.add(person)
            session.flush()
            person_id = person.id
            event = create_event(session, 51)
            vector = np.array([1, 0, 0, 0], dtype=np.float32)
            cluster = UnknownCluster(
                model_id=settings.model_id,
                centroid=pack_vector(vector),
                embedding_dimension=4,
                status="review_ready",
                member_count=1,
                event_count=1,
                distinct_days=1,
                high_quality_count=1,
            )
            session.add(cluster)
            session.flush()
            cluster_id = cluster.id
            track = FaceTrack(
                event_id=event.id,
                track_index=0,
                model_id=settings.model_id,
                embedding=pack_vector(vector),
                embedding_dimension=4,
                quality_score=0.93,
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

        assigned = client.post(
            f"/api/clusters/{cluster_id}/assign-person",
            headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
            json={
                "target_person_id": person_id,
                "idempotency_key": "api-assign-cluster-person-0001",
            },
        )
        assert assigned.status_code == 200

        operations = client.get("/api/operations")
        assert operations.status_code == 200
        item = operations.json()["items"][0]
        assert item["operation"] == "assign_cluster_to_person"
        assert item["operation_label"] == "并入已确认人物"
        assert "已确认人物：已确认人物甲" in item["subject_label"]
        assert cluster_id not in item["subject_label"]


def test_cluster_review_includes_video_and_supports_byte_ranges(settings):
    _migrate_for_test(settings)
    video_path = settings.data_dir / "review-sample.mp4"
    video_bytes = b"0123456789abcdef"
    video_path.write_bytes(video_bytes)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        accepted = client.post(
            "/api/session/login",
            json={"password": "correct horse battery staple"},
        )
        assert accepted.status_code == 200
        with app.state.runtime.database.session() as session:
            artifact = ArtifactManifest(
                artifact_type="source_video",
                logical_path="review/sample.mp4",
                local_path=str(video_path),
                size_bytes=len(video_bytes),
                sha256=hashlib.sha256(video_bytes).hexdigest(),
                retention_class="event_35d",
            )
            session.add(artifact)
            session.flush()
            artifact_id = artifact.id
            event = create_event(session, 50)
            event.source_artifact_id = artifact.id
            event.duration_seconds = 6.5
            vector = np.array([1, 0, 0, 0], dtype=np.float32)
            cluster = UnknownCluster(
                model_id=settings.model_id,
                centroid=pack_vector(vector),
                embedding_dimension=4,
                status="review_ready",
                member_count=1,
                event_count=1,
                distinct_days=1,
                high_quality_count=1,
            )
            session.add(cluster)
            session.flush()
            track = FaceTrack(
                event_id=event.id,
                track_index=0,
                model_id=settings.model_id,
                embedding=pack_vector(vector),
                embedding_dimension=4,
                quality_score=0.93,
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

        response = client.get("/api/clusters")
        assert response.status_code == 200
        sample = response.json()["items"][0]["tracks"][0]
        assert sample["video_url"] == f"/api/artifacts/{artifact_id}"
        assert sample["duration_seconds"] == 6.5
        assert sample["occurred_at"]

        ranged = client.get(
            sample["video_url"],
            headers={"Range": "bytes=0-3"},
        )
        assert ranged.status_code == 206
        assert ranged.content == video_bytes[:4]
        assert ranged.headers["content-range"] == f"bytes 0-3/{len(video_bytes)}"
