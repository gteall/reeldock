import time

from fastapi.testclient import TestClient
from p2_support import FakeTMDB, MemoryDAV
from sqlalchemy import select

from reeldock.app import create_app
from reeldock.models import MovieRecord
from reeldock.security import init_admin


def test_p2_movie_api_match_preview_and_auth(runtime, config_body):
    dav, tmdb = MemoryDAV(), FakeTMDB()
    app = create_app(runtime, storage_factory=dav, metadata_factory=tmdb)
    with TestClient(app) as client:
        assert client.get("/api/movies").status_code == 401
        init_admin(app.state.db, "admin", "p2-integration-password")
        login = client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost:8000"},
            json={"username": "admin", "password": "p2-integration-password"},
        )
        client.headers.update(
            {"Origin": "http://localhost:8000", "X-CSRF-Token": login.json()["csrf_token"]}
        )
        config_body["stable_seconds"] = 1
        assert client.put("/api/config", json=config_body).status_code == 200
        worker, queue = app.state.worker, app.state.queue
        import asyncio

        def execute():
            asyncio.run(worker.execute(queue.claim("api-test")))

        for number in [1, 2]:
            assert (
                client.post("/api/scan", json={"idempotency_key": f"scan-{number}"}).status_code
                == 202
            )
            execute()
            if number == 1:
                with app.state.db.sessions.begin() as session:
                    session.scalar(select(MovieRecord)).stable_since = time.time() - 2
        movie = client.get("/api/movies").json()["items"][0]
        assert movie["scan_status"] == "stable"
        assert (
            client.put(f"/api/movies/{movie['id']}/match", json={"tmdb_id": "bad"}).status_code
            == 422
        )
        assert (
            client.put(
                f"/api/movies/{movie['id']}/match",
                json={"tmdb_id": "123"},
                headers={"X-CSRF-Token": "invalid"},
            ).status_code
            == 403
        )
        assert (
            client.put(f"/api/movies/{movie['id']}/match", json={"tmdb_id": "123"}).status_code
            == 200
        )
        result = client.post(
            "/api/tasks", json={"package_path": movie["path"], "idempotency_key": "p2-task"}
        )
        execute()
        task = client.get("/api/tasks/" + result.json()["task_id"]).json()
        assert task["status"] == "completed", task
        movie = client.get(f"/api/movies/{movie['id']}").json()
        assert movie["original_language"] == "en" and movie["subtitle_status"] == "pending"
        nfo = next(a for a in movie["assets"] if a["kind"] == "nfo")
        preview = client.get(nfo["preview"])
        assert preview.status_code == 200 and "text/plain" in preview.headers["content-type"]
        assert b"<movie>" in preview.content and b"streamdetails" not in preview.content
        poster = next(a for a in movie["assets"] if a["kind"] == "poster")
        assert client.get(poster["preview"]).content.startswith(b"\xff\xd8")
        assert dav.calls["media_reads"] == dav.calls["move"] == 0
        assert "tmdb-private-token" not in str(movie)
        client.post("/api/auth/logout")
        assert client.get(nfo["preview"]).status_code == 401


def test_upgrade_existing_p1_configuration_and_tasks(tmp_path, config_body):
    import json
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from reeldock import migrations
    from reeldock.database import Database
    from reeldock.models import Configuration, Task
    from reeldock.security import SettingsStore, Vault

    db = Database(tmp_path / "upgrade.sqlite3")
    alembic = Config()
    alembic.set_main_option("script_location", str(Path(migrations.__path__[0])))
    with db.engine.begin() as connection:
        alembic.attributes["connection"] = connection
        command.upgrade(alembic, "0001")
    vault = Vault(tmp_path)
    with db.sessions.begin() as session:
        session.add(
            Configuration(
                id=1,
                revision=1,
                encrypted=vault.cipher.encrypt(json.dumps(config_body).encode()).decode(),
            )
        )
    # Seed the old schema through SQL, not the current ORM's added P3 columns.
    with db.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO packages (id, remote_path, kind, source_snapshot, context_version, "
            "policy_version, base_required, base_status, subtitle_status, lease_generation) "
            "VALUES ('old-package', ':connection', 'connection', '{}', 1, 1, '[]', "
            "'pending', 'pending', 0)"
        )
        connection.exec_driver_sql(
            "INSERT INTO tasks (id, package_id, kind, idempotency_key, payload_hash, "
            "context_version, config_revision, status, stage, attempts, max_attempts, "
            "retry_at, pause_requested, created_at, updated_at) VALUES ('old-task', "
            "'old-package', 'connection_check', 'p1-existing', 'hash', 1, 1, 'queued', "
            "'connection_check', 0, 3, 0, 0, 0, 0)"
        )
    task_id = "old-task"
    db.migrate()
    db.migrate()
    config, revision = SettingsStore(db, vault).load()
    assert revision == 1 and config.stable_seconds == 600 and config.actor_limit == 20
    with db.sessions.begin() as session:
        assert session.get(Task, task_id).status == "queued"
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0004"
    db.close()
