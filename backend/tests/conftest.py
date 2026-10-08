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


@pytest.fixture(autouse=True)
def p2_forbidden_services(request, monkeypatch):
    """P2 cannot launch a probe process or contact Shooter, including error paths."""
    if not request.node.path.name.startswith("test_p2_"):
        return
    import asyncio
    import subprocess

    import httpx

    def process_forbidden(*args, **kwargs):
        pytest.fail("P2 must not launch FFprobe, FFmpeg or any media subprocess")

    async def async_process_forbidden(*args, **kwargs):
        process_forbidden()

    original_send = httpx.AsyncClient.send

    async def send(client, request, *args, **kwargs):
        if "shooter" in request.url.host:
            pytest.fail("P2 must not contact Subtitle Provider")
        return await original_send(client, request, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", process_forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", async_process_forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", async_process_forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
