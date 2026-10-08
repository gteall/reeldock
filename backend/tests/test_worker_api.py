import time

from fastapi.testclient import TestClient

from reeldock.app import create_app
from reeldock.domain import ProviderError, RemoteEntry
from reeldock.security import init_admin


def test_real_worker_connection_task_and_restart(runtime, config_body):
    calls = []

    class FakeStorage:
        def __init__(self, config):
            self.config = config

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def list(self, path):
            calls.append(path)
            return [RemoteEntry(path=path + "/entry", is_dir=False, size=12)]

        async def read_range(self, *args):
            raise AssertionError("P1 cannot read media")

        async def move(self, *args):
            raise AssertionError("P1 cannot MOVE")

        async def put(self, *args):
            raise AssertionError("P1 cannot PUT")

    runtime.worker_enabled, runtime.poll_seconds = True, 0.05
    app = create_app(runtime, storage_factory=FakeStorage)
    with TestClient(app) as client:
        init_admin(app.state.db, "admin", "worker-integration-password")
        logged = client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost:8000"},
            json={"username": "admin", "password": "worker-integration-password"},
        )
        client.headers.update(
            {"Origin": "http://localhost:8000", "X-CSRF-Token": logged.json()["csrf_token"]}
        )
        client.put("/api/config", json=config_body)
        body = {"idempotency_key": "one-connection-intent"}
        response = client.post("/api/connection-check", json=body)
        task_id = response.json()["task_id"]
        assert client.post("/api/connection-check", json=body).json()["task_id"] == task_id
        for _ in range(100):
            task = client.get(f"/api/tasks/{task_id}").json()
            if task["status"] == "completed":
                break
            time.sleep(0.02)
        assert task["status"] == "completed"
        assert task["steps"][0]["checkpoint"]["mode"] == "read_only"
        cookies = dict(client.cookies)
    with TestClient(create_app(runtime, storage_factory=FakeStorage)) as restarted:
        restarted.cookies.update(cookies)
        assert restarted.get(f"/api/tasks/{task_id}").json()["status"] == "completed"
        assert restarted.get("/api/config").json()["configured"]
        assert restarted.get("/api/events").json()["next_cursor"] > 0
    assert calls == ["/incoming", "/library"]


def test_connection_failure_is_safe_and_persisted(client, config_body):
    client.put("/api/config", json=config_body)
    task_id = client.post("/api/connection-check", json={"idempotency_key": "denied"}).json()[
        "task_id"
    ]
    queue = client.app.state.queue
    claim = queue.claim("test")
    queue.begin_step(claim)
    queue.fail(claim, ProviderError("storage_permission_denied"))
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "blocked" and task["error_code"] == "storage_permission_denied"
