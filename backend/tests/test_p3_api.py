import time

from fastapi.testclient import TestClient
from p2_support import FakeTMDB
from p3_support import DAV, Probe, Shooter

from reeldock.app import create_app
from reeldock.security import init_admin


async def test_full_task_api_states_and_authenticated_manifest(runtime, config_body):
    dav, metadata = DAV(), FakeTMDB()
    metadata.movies[0].original_language = "zh"
    probe, shooter = Probe(dav), Shooter(dav)
    app = create_app(
        runtime,
        storage_factory=dav,
        metadata_factory=metadata,
        probe=probe,
        subtitle_factory=shooter,
    )
    with TestClient(app) as client:
        init_admin(app.state.db, "admin", "p3-test-admin-password")
        response = client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost:8000"},
            json={"username": "admin", "password": "p3-test-admin-password"},
        )
        client.headers.update(
            {"Origin": "http://localhost:8000", "X-CSRF-Token": response.json()["csrf_token"]}
        )
        assert client.put("/api/config", json={**config_body}).status_code == 200
        config, revision = app.state.store.load()
        await app.state.worker.movies.scanner.scan(dav, config, revision, now=time.time() - 601)
        await app.state.worker.movies.scanner.scan(dav, config, revision)
        body = {
            "kind": "package_pipeline",
            "package_path": "/incoming/Example (2020)",
            "idempotency_key": "p3-full",
        }
        task = client.post("/api/tasks", json=body).json()["task_id"]
        assert client.post("/api/tasks", json=body).json()["task_id"] == task
        await app.state.worker.execute(app.state.queue.claim("test"))
        movie = client.get("/api/movies").json()["items"][0]
        assert movie["subtitle_status"] == "skipped_tmdb_chinese"
        assert movie["probe_status"] == "not_started" and movie["probe_evidence"] == {}
        assert movie["scrape_completed"] and movie["path"] == body["package_path"]
        assert movie["archive_intent"] is None and not dav.calls["move"]
        manifest = next(a for a in movie["assets"] if a["kind"] == "manifest" and a["required"])
        assert client.get(manifest["preview"]).json()["package_id"] == movie["id"]
        assert (
            client.put("/api/movies/" + movie["id"] + "/match", json={"tmdb_id": "124"}).status_code
            == 200
        )
        client.post("/api/auth/logout")
        assert client.get(manifest["preview"]).status_code == 401
        assert not probe.calls and not shooter.calls
