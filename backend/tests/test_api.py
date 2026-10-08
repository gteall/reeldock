import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from reeldock.app import create_app
from reeldock.models import Configuration
from reeldock.security import SafeFormatter, SettingsStore, Vault


def test_config_persists_encrypted_and_session_survives_restart(client, runtime, config_body):
    saved = client.put("/api/config", json=config_body)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert saved.json()["webdav_password_set"]
    assert "private" not in saved.text
    with client.app.state.db.sessions.begin() as session:
        encrypted = session.get(Configuration, 1).encrypted
        assert "dav.example" not in encrypted and "private" not in encrypted
    with TestClient(create_app(runtime)) as restarted:
        restarted.cookies.update(client.cookies)
        loaded = restarted.get("/api/config")
        assert loaded.json() == saved.json()
        auth = restarted.get("/api/auth/session").json()
        body = {**config_body, "webdav_password": "", "tmdb_token": "", "expected_revision": 1}
        headers = {"Origin": "http://localhost:8000", "X-CSRF-Token": auth["csrf_token"]}
        assert restarted.put("/api/config", json=body, headers=headers).status_code == 200
        config, revision = restarted.app.state.store.load()
        assert config.webdav_password.get_secret_value() == config_body["webdav_password"]
        assert revision == 2
        body.update(expected_revision=2, clear_tmdb_token=True)
        assert restarted.put("/api/config", json=body, headers=headers).status_code == 200
        assert not restarted.get("/api/config").json()["tmdb_token_set"]
        assert restarted.put("/api/config", json=body, headers=headers).status_code == 409
    assert (runtime.data_dir / "master.key").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("input_path", "output_path"),
    [
        ("/same/", "/same"),
        ("/a", "/a/sub"),
        ("/a/sub", "/a"),
        ("/", "/b"),
        ("/a", "/"),
        ("/a/../b", "/c"),
        ("/a/%252e%252e/b", "/c"),
        ("relative", "/c"),
        ("/a", "/%61/sub"),
        ("/a\\bad", "/c"),
    ],
)
def test_invalid_paths_rejected_without_persisting(client, config_body, input_path, output_path):
    response = client.put(
        "/api/config", json={**config_body, "input_path": input_path, "output_path": output_path}
    )
    assert response.status_code == 422
    assert "webdav-private-secret" not in response.text
    assert client.get("/api/config").json()["revision"] == 0


def test_valid_special_paths_and_no_credential_urls(client, config_body):
    body = {**config_body, "input_path": "/待刮削 #100%", "output_path": "/影库"}
    assert client.put("/api/config", json=body).status_code == 200
    body.update(expected_revision=1, webdav_url="https://user:secret@dav.example/dav/")
    response = client.put("/api/config", json=body)
    assert response.status_code == 422 and "user:secret" not in response.text


def test_auth_origin_csrf_and_logout(client, config_body):
    assert (
        client.put(
            "/api/config", json=config_body, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.put("/api/config", json=config_body, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert (
        client.put(
            "/api/config", json=config_body, headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    client.headers.pop("Origin")
    assert client.put("/api/config", json=config_body).status_code == 403
    client.headers["Origin"] = "http://localhost:8000"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/health").status_code == 200


def test_login_origin_and_rate_limit(client):
    body = {"username": "admin", "password": "incorrect-secret"}
    assert (
        client.post(
            "/api/auth/login", json=body, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    for _ in range(5):
        assert client.post("/api/auth/login", json=body).status_code == 401
    assert client.post("/api/auth/login", json=body).status_code == 429


def test_duplicate_tasks_and_recoverable_events(client, config_body):
    client.put("/api/config", json=config_body)
    body = {"package_path": "/incoming/电影", "idempotency_key": "stable-intent-key"}
    one = client.post("/api/tasks", json=body)
    two = client.post("/api/tasks", json=body)
    assert one.status_code == 202 and one.json() == two.json()
    assert len(client.get("/api/tasks").json()["items"]) == 1
    assert (
        client.post("/api/tasks", json={**body, "package_path": "/incoming/other"}).status_code
        == 409
    )
    assert client.post("/api/tasks", json={**body, "package_path": "/incoming"}).status_code == 422
    assert (
        client.post("/api/tasks", json={**body, "package_path": "/library/movie"}).status_code
        == 422
    )
    page = client.get("/api/events?after=0&limit=1").json()
    more = client.get(f"/api/events?after={page['next_cursor']}&limit=1").json()
    assert page["items"][0]["id"] < more["items"][0]["id"]
    assert "private" not in str(page) + str(more)
    task_id = one.json()["task_id"]
    assert client.post(f"/api/tasks/{task_id}/pause").status_code == 200
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "paused"
    assert client.post(f"/api/tasks/{task_id}/resume").status_code == 200


def test_migration_repeated_and_schema_matches(foundation):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from reeldock.models import Base

    db, _, _ = foundation
    db.migrate()
    with db.engine.begin() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
        )


def test_missing_key_fails_closed(foundation, tmp_path):
    db, _, _ = foundation
    different = tmp_path / "different-key"
    with pytest.raises(RuntimeError, match="credential_key_mismatch"):
        SettingsStore(db, Vault(different)).load()


def test_missing_key_on_restart_does_not_silently_generate_replacement(
    client, runtime, config_body
):
    assert client.put("/api/config", json=config_body).status_code == 200
    key = runtime.data_dir / "master.key"
    key.unlink()
    with pytest.raises(RuntimeError, match="credential_key_missing"):
        with TestClient(create_app(runtime)):
            pass
    assert not key.exists()


def test_logs_never_serialize_messages_exceptions_or_extra():
    record = logging.LogRecord(
        "httpx", logging.ERROR, "", 0, "https://private/?token=secret", (), None
    )
    record.secret = "password-value"
    formatted = SafeFormatter().format(record)
    assert "secret" not in formatted and "password" not in formatted and "private" not in formatted
    record.safe_code = "storage_timeout"
    assert "storage_timeout" in SafeFormatter().format(record)
