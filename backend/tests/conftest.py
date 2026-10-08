import pytest
from fastapi.testclient import TestClient

from reeldock.app import create_app
from reeldock.database import Database
from reeldock.domain import ConfigUpdate
from reeldock.queue import Queue
from reeldock.runtime import Runtime
from reeldock.security import SettingsStore, Vault, init_admin

PASSWORD = "local-test-password-strong"


@pytest.fixture
def config_body():
    return {
        "webdav_url": "https://dav.example/dav/",
        "webdav_username": "test",
        "webdav_password": "webdav-private-secret",
        "tmdb_token": "tmdb-private-token",
        "input_path": "/incoming",
        "output_path": "/library",
    }


@pytest.fixture
def foundation(tmp_path, config_body):
    db = Database(tmp_path / "db.sqlite3")
    db.migrate()
    store = SettingsStore(db, Vault(tmp_path))
    store.save(ConfigUpdate(**config_body))
    queue = Queue(db)
    yield db, store, queue
    db.close()


@pytest.fixture
def runtime(tmp_path):
    return Runtime(data_dir=tmp_path, worker_enabled=False)


@pytest.fixture
def client(runtime):
    app = create_app(runtime)
    with TestClient(app) as client:
        init_admin(app.state.db, "admin", PASSWORD)
        response = client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost:8000"},
            json={"username": "admin", "password": PASSWORD},
        )
        assert response.status_code == 200
        client.headers.update(
            {"Origin": "http://localhost:8000", "X-CSRF-Token": response.json()["csrf_token"]}
        )
        yield client
