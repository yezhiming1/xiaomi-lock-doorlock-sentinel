from fastapi.testclient import TestClient

from doorlock_sentinel.api import create_app
from doorlock_sentinel.db import Database
from doorlock_sentinel.models import Base, LoginThrottle


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
        assert client.put(
            "/api/settings/notifications",
            json={
                "identity_notifications_enabled": False,
                "risk_notifications_enabled": False,
            },
        ).status_code == 403
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
        assert client.get(
            "/internal/outbox/claim?worker=test",
            headers={"X-Internal-Token": "test-internal-secret"},
        ).status_code == 200


def test_security_headers_cover_static_console(settings):
    _migrate_for_test(settings)
    app = create_app(settings)
    with TestClient(app, base_url="http://testserver") as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert client.get("/app.js").headers["cache-control"] == "no-store"
