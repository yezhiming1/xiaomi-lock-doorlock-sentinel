import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from doorlock_sentinel.api import create_app
from doorlock_sentinel.cloud_playback import validate_playback_url
from doorlock_sentinel.db import Database
from doorlock_sentinel.models import ArtifactManifest, BackupReceipt, Base


@pytest.mark.parametrize("local_present", [True, False])
def test_video_redirect_ignores_local_presence(settings, monkeypatch, local_present):
    db = Database(settings)
    Base.metadata.create_all(db.engine)
    path = settings.inbox_dir / "synthetic.mp4"
    if local_present:
        path.write_bytes(b"must never be served locally")
    with db.session() as session:
        artifact = ArtifactManifest(
            id="synthetic-video", artifact_type="source_video", logical_path="test/video",
            local_path=str(path), size_bytes=12, sha256="f" * 64, backup_state="verified",
            retention_class="event_35d",
        )
        session.add(artifact)
        session.flush()
        session.add(BackupReceipt(
            artifact_id=artifact.id, state="verified", remote_sha256="f" * 64,
            remote_size_bytes=12, remote_locator="/backups/test/synthetic.mp4",
        ))
    seen = []

    def resolve(sock, path, size, ua):
        seen.append((path, size, ua))
        return "https://test.115cdn.net/synthetic.mp4?expires=123"

    monkeypatch.setattr("doorlock_sentinel.web_data.resolve_video", resolve)
    with TestClient(create_app(settings), base_url="http://testserver") as client:
        assert client.get("/api/artifacts/synthetic-video").status_code == 401
        assert not seen
        client.post("/api/session/login", json={"password": "correct horse battery staple"})
        response = client.get("/api/artifacts/synthetic-video", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].startswith("https://test.115cdn.net/")
        assert response.headers["cache-control"] == "no-store"
        assert len(seen) == 1 and seen[0][:2] == ("/backups/test/synthetic.mp4", 12)
        def unavailable(*args):
            raise OSError("synthetic service outage")
        monkeypatch.setattr("doorlock_sentinel.web_data.resolve_video", unavailable)
        assert client.get("/api/artifacts/synthetic-video").status_code == 503
        with db.session() as session:
            session.query(BackupReceipt).delete()
        assert client.get("/api/artifacts/synthetic-video").status_code == 409
        assert len(seen) == 1
    db.engine.dispose()


@pytest.mark.parametrize("url", [
    "http://test.115cdn.net/file", "https://115cdn.net.evil.invalid/file",
    "https://user@test.115cdn.net/file", "https://test.115cdn.net:444/file",
    "https://127.0.0.1/file", "file:///video.mp4",
])
def test_reject_untrusted_playback_url(url):
    with pytest.raises(ValueError):
        validate_playback_url(url)


def load_resolver():
    path = Path(__file__).parents[1] / "services/cloud-playback/resolver.py"
    spec = importlib.util.spec_from_file_location("isolated_cloud_resolver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolver_exact_path_cache_ua_and_no_file_writes(tmp_path):
    module = load_resolver()
    state = tmp_path / "state.json"
    state.write_text('{"access_token":"synthetic","expires_at":9999999999}')
    resolver = module.Resolver(state, "/backups/test")
    calls = []

    def request(endpoint, fields, ua):
        calls.append((endpoint, fields, ua))
        if endpoint.endswith("get_info"):
            return {"file_name": "test.mp4", "size_byte": 12, "pick_code": "synthetic"}
        return {"synthetic": {"url": {"url": "https://test.115cdn.net/video"}}}

    resolver.request = request
    payload = {"path": "/backups/test/test.mp4", "size": 12, "user_agent": "browserA"}
    assert resolver.resolve(payload) == "https://test.115cdn.net/video"
    assert resolver.resolve(payload) == "https://test.115cdn.net/video"
    assert len(calls) == 2
    resolver.resolve({**payload, "user_agent": "browserB"})
    assert len(calls) == 4 and calls[-1][2] == "browserB"
    for invalid in ["/backups/other/test.mp4", "/backups/test/../test.mp4", "https://evil/file"]:
        with pytest.raises(module.ResolverError):
            resolver.resolve({**payload, "path": invalid})
    assert len(calls) == 4
    assert all(c[0] in ("/open/folder/get_info", "/open/ufile/downurl") for c in calls)
    assert state.read_text() == '{"access_token":"synthetic","expires_at":9999999999}'


def test_resolver_mismatch_stops_before_downurl(tmp_path):
    module = load_resolver()
    state = tmp_path / "state.json"
    state.write_text('{"access_token":"synthetic","expires_at":9999999999}')
    resolver = module.Resolver(state, "/backups/test")
    calls = []

    def request(endpoint, fields, ua):
        calls.append(endpoint)
        return {"file_name": "different.mp4", "size_byte": 12}

    resolver.request = request
    with pytest.raises(module.ResolverError):
        resolver.resolve({"path": "/backups/test/test.mp4", "size": 12, "user_agent": "browser"})
    assert calls == ["/open/folder/get_info"]
