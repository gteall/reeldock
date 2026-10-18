"""Real HTTP/SSE + durable DB/Worker, in-memory remote providers only."""

import asyncio
import socket
import time

import httpx
import uvicorn
from p3_support import DAV
from p4_support import ROOT, TVMetadata, TVProbe, TVShooter

from reeldock.app import create_app
from reeldock.domain import ConfigUpdate
from reeldock.security import init_admin


async def test_http_worker_sse_disconnect_replay_and_state_refetch(runtime, config_body):
    dav = DAV()
    dav.media = {ROOT + "/Example.S01E01.mkv": 1024**3, ROOT + "/Example.S01E02.mkv": 1024**3}
    probe, shooter = TVProbe(dav), TVShooter(dav)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        app = create_app(
            runtime.model_copy(update={"worker_enabled": True, "allowed_origins": [base]}),
            storage_factory=dav,
            metadata_factory=TVMetadata(),
            probe=probe,
            subtitle_factory=shooter,
        )
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        job = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with httpx.AsyncClient(
                base_url=base, headers={"Origin": base}, trust_env=False, timeout=5
            ) as client:
                for _ in range(100):
                    if server.started:
                        break
                    await asyncio.sleep(0.02)
                assert server.started
                init_admin(app.state.db, "admin", "p4-http-test-password")
                app.state.store.save(
                    ConfigUpdate(**{**config_body, "stable_seconds": 1, "move_verified": True})
                )
                auth = await client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "p4-http-test-password"},
                )
                client.headers["X-CSRF-Token"] = auth.json()["csrf_token"]
                config, revision = app.state.store.load()
                await app.state.worker.movies.scanner.scan(
                    dav, config, revision, now=time.time() - 2
                )
                await app.state.worker.movies.scanner.scan(dav, config, revision)
                cursor = 0
                async with client.stream("GET", "/api/events/stream") as response:
                    assert response.status_code == 200 and response.headers[
                        "content-type"
                    ].startswith("text/event-stream")
                    async for line in response.aiter_lines():
                        if line.startswith("id: "):
                            cursor = int(line[4:])
                            break
                # The client is disconnected while the Worker completes the task.
                submitted = await client.post(
                    "/api/tasks",
                    json={
                        "kind": "package_pipeline",
                        "package_path": ROOT,
                        "idempotency_key": "http-p4",
                    },
                )
                task_id = submitted.json()["task_id"]
                for _ in range(150):
                    task = (await client.get("/api/tasks/" + task_id)).json()
                    if task["status"] in {"completed", "blocked"}:
                        break
                    await asyncio.sleep(0.05)
                assert task["status"] == "completed", task
                async with client.stream(
                    "GET", "/api/events/stream", headers={"Last-Event-ID": str(cursor)}
                ) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("id: "):
                            assert int(line[4:]) > cursor
                            break
                refreshed = (await client.get("/api/packages")).json()["items"][0]
                assert refreshed["archive_status"] == "archived"
                assert len(refreshed["media"]) == 2
                assert all(
                    m["subtitle_status"] == "skipped_tmdb_chinese" for m in refreshed["media"]
                )
                assert not probe.calls and not shooter.calls and not dav.calls["media_reads"]
        finally:
            server.should_exit = True
            await asyncio.wait_for(job, 10)
