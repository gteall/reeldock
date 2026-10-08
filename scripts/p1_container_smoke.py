"""Repeatable container restart smoke test, using a fresh isolated Docker volume.

No actual WebDAV endpoint is used. Requires a locally built reeldock-app image.
"""

import argparse
import json
import secrets
import subprocess
import time

import httpx


def docker(*args, input=None):
    return subprocess.run(
        ["docker", *args], input=input, text=True, capture_output=True, check=True
    )


def healthy(client):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            response = client.get("/api/health")
            if response.status_code == 200:
                assert response.json()["archive_enabled"] is False
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("container_health_timeout")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="reeldock-app")
    parser.add_argument("--port", type=int, default=18001)
    args = parser.parse_args()
    suffix = secrets.token_hex(5)
    name, volume = "reeldock-p1-smoke-" + suffix, "reeldock-p1-smoke-" + suffix
    base = f"http://127.0.0.1:{args.port}"
    password, remote_secret = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    checks = []
    try:
        docker("volume", "create", volume)
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{args.port}:8000",
            "-v",
            volume + ":/data",
            "-e",
            "REELDOCK_ALLOWED_ORIGINS=" + json.dumps([base]),
            args.image,
        )
        with httpx.Client(
            base_url=base, timeout=3, headers={"Origin": base}, trust_env=False
        ) as client:
            healthy(client)
            checks.append("container_health")
            assert 'id="root"' in client.get("/").text
            checks.append("static_frontend")
            docker(
                "exec",
                "-i",
                name,
                "/app/.venv/bin/reeldock",
                "init-admin",
                "--password-stdin",
                input=password + "\n",
            )
            login = client.post("/api/auth/login", json={"username": "admin", "password": password})
            assert login.status_code == 200
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            config = {
                "webdav_url": "https://dav.example/dav/",
                "input_path": "/incoming",
                "output_path": "/library",
                "webdav_password": remote_secret,
            }
            saved = client.put("/api/config", json=config)
            assert saved.status_code == 200 and remote_secret not in saved.text
            checks.append("encrypted_configuration")
            body = {"package_path": "/incoming/Generated", "idempotency_key": "one-smoke-intent"}
            task = client.post("/api/tasks", json=body)
            assert task.status_code == 202
            task_id = task.json()["task_id"]
            assert client.post("/api/tasks", json=body).json()["task_id"] == task_id
            checks.append("idempotent_submission")
            assert client.post(f"/api/tasks/{task_id}/pause").status_code == 200
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if client.get(f"/api/tasks/{task_id}").json()["status"] == "paused":
                    break
                time.sleep(0.05)
            assert client.get(f"/api/tasks/{task_id}").json()["status"] == "paused"
            before = client.get("/api/events").json()["next_cursor"]
            docker("restart", name)
            healthy(client)
            assert client.get("/api/config").json() == saved.json()
            assert client.get(f"/api/tasks/{task_id}").json()["status"] == "paused"
            assert client.get("/api/events").json()["next_cursor"] >= before
            checks += [
                "session_after_restart",
                "configuration_after_restart",
                "paused_task_after_restart",
                "events_after_restart",
            ]
            assert client.post(f"/api/tasks/{task_id}/resume").status_code == 200
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = client.get(f"/api/tasks/{task_id}").json()
                if result["status"] == "blocked":
                    break
                time.sleep(0.05)
            assert result["error_code"] == "stage_not_implemented_p1"
            checks.append("honest_p1_stage_boundary")
        print(json.dumps({"status": "passed", "checks": checks, "real_media_moves": 0}))
    finally:
        # Only containers/volumes created by this invocation are removed.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


if __name__ == "__main__":
    main()
