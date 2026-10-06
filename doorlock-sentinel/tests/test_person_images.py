from conftest import create_event
from fastapi.testclient import TestClient

from doorlock_sentinel.api import create_app
from doorlock_sentinel.db import Database
from doorlock_sentinel.models import ArtifactManifest, Base, FaceTrack, Person


def test_people_images_use_same_representative_track_and_remain_authenticated(settings):
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    with database.session() as session:
        person = Person(display_name="合成人物", relationship="friend", status="known")
        empty = Person(display_name="无样本", relationship="other", status="known")
        session.add_all([person, empty])
        session.flush()
        for index, representative, quality in [(0, True, 0.8), (1, False, 0.99)]:
            for kind in ("face", "scene"):
                session.add(
                    ArtifactManifest(
                        id=f"{kind}-{index}",
                        artifact_type="face_crop" if kind == "face" else "scene_frame",
                        logical_path=f"synthetic/{kind}-{index}.jpg",
                        local_path=str(settings.data_dir / f"{kind}-{index}.jpg"),
                        size_bytes=1,
                        sha256="a" * 64,
                        retention_class="event_35d",
                    )
                )
            session.flush()
            event = create_event(session, 20 + index)
            session.add(
                FaceTrack(
                    event_id=event.id,
                    track_index=0,
                    person_id=person.id,
                    model_id=settings.model_id,
                    quality_score=quality,
                    embedding=b"synthetic",
                    embedding_dimension=4,
                    representative=representative,
                    best_face_artifact_id=f"face-{index}",
                    best_frame_artifact_id=f"scene-{index}",
                )
            )
    database.engine.dispose()
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        assert client.get("/api/people").status_code == 401
        assert client.get("/api/artifacts/scene-0").status_code == 401
        assert (
            client.post(
                "/api/session/login",
                json={
                    "password": "correct horse battery staple",
                },
            ).status_code
            == 200
        )
        items = client.get("/api/people").json()["items"]
        person = next(item for item in items if item["display_name"] == "合成人物")
        assert person["face_url"] == "/api/artifacts/face-0"
        assert person["preview_url"] == "/api/artifacts/scene-0"
        empty = next(item for item in items if item["display_name"] == "无样本")
        assert empty["face_url"] is None and empty["preview_url"] is None
