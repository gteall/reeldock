"""Production-image checks in isolated Docker volumes; never uses real NAS credentials.

Mock remote services are explicitly injected only in the mounted fixture process.
Actual image, FastAPI, SQLite, queue, assets, probe/FFmpeg and recovery code are used.
"""

import argparse
import json
import secrets
import subprocess
import time
from pathlib import Path

import httpx


class Harness:
    def __init__(self, args):
        self.args = args
        self.suffix = secrets.token_hex(5)
        self.name = "reeldock-p5-" + self.suffix
        self.volumes = []
        self.repo = str(args.fixture_root.resolve())
        self.base = f"http://127.0.0.1:{args.port}"
        self.uid = "12001:12001"

    def docker(self, *args, input=None, check=True):
        result = subprocess.run(
            ["docker", *args], input=input, text=True, capture_output=True, timeout=120
        )
        if check and result.returncode:
            raise RuntimeError("docker_command_failed")
        return result

    def volume(self, suffix):
        name = self.name + "-" + suffix
        self.docker("volume", "create", name)
        self.volumes.append(name)
        self.docker(
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--user",
            "0:0",
            "-v",
            name + ":/prepare:nocopy",
            "--entrypoint",
            "python",
            self.args.image,
            "-c",
            'import os; os.chown("/prepare",12001,12001); os.chmod("/prepare",0o700)',
        )
        return name

    def start(self, data, cache, remote=None):
        self.docker("rm", "-f", self.name, check=False)
        command = [
            "run",
            "-d",
            "--platform",
            "linux/amd64",
            "--name",
            self.name,
            "--user",
            self.uid,
            "--init",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m,mode=1777",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=3",
            "-p",
            f"127.0.0.1:{self.args.port}:8000",
            "-v",
            data + ":/data:nocopy",
            "-v",
            cache + ":/cache:nocopy",
            "-e",
            "REELDOCK_ALLOWED_ORIGINS=" + json.dumps([self.base]),
            "-e",
            "REELDOCK_LEASE_SECONDS=3",
            "-e",
            "REELDOCK_POLL_SECONDS=0.1",
        ]
        if remote:
            command += [
                "-v",
                remote + ":/mock-remote:nocopy",
                "-v",
                self.repo + "/scripts:/validation/scripts:ro",
                "-v",
                self.repo + "/backend/tests:/validation/tests:ro",
                "--entrypoint",
                "python",
                self.args.image,
                "/validation/scripts/p5_fixture.py",
            ]
        else:
            command += [self.args.image]
        self.docker(*command)

    def execute(self, *command, input=None, check=True):
        return self.docker("exec", "-i", self.name, *command, input=input, check=check)

    def state(self):
        return json.loads(
            self.execute("python", "-c", 'print(open("/mock-remote/state.json").read())').stdout
        )

    def marker(self, name, text=""):
        self.execute(
            "python",
            "-c",
            (
                "from pathlib import Path; import sys; "
                'Path("/mock-remote/"+sys.argv[1]).write_text(sys.stdin.read())'
            ),
            name,
            input=text,
        )

    def cleanup(self):
        self.docker("rm", "-f", self.name, check=False)
        for name in reversed(self.volumes):
            self.docker("volume", "rm", name, check=False)


def wait_for(test, code, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = test()
            if value:
                return value
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError(code)


def healthy(client):
    return wait_for(lambda: client.get("/api/health").status_code == 200, "health_timeout")


def login(client, password):
    response = client.post("/api/auth/login", json={"username": "admin", "password": password})
    assert response.status_code == 200, "login_failed"
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def task(client, identifier):
    value = client.get("/api/tasks/" + identifier).json()
    return value if value["status"] in {"completed", "blocked", "failed", "paused"} else None


def submit(client, path, key):
    response = client.post(
        "/api/tasks",
        json={"kind": "package_pipeline", "package_path": path, "idempotency_key": key},
    )
    assert response.status_code == 202, "submit_failed"
    return response.json()["task_id"]


def wait_task(client, identifier, status, stage=None):
    result = wait_for(lambda: task(client, identifier), "task_timeout")
    assert result["status"] == status, "unexpected_task_status:" + str(result.get("error_code"))
    if stage:
        assert result["stage"] == stage, "unexpected_task_stage"
    return result


def run(args, checks):
    harness = Harness(args)
    password = secrets.token_urlsafe(24)
    try:
        architecture = harness.docker(
            "image", "inspect", args.image, "--format", "{{.Architecture}}"
        ).stdout.strip()
        assert architecture == "amd64", "amd64_image_required"
        data, cache = harness.volume("data"), harness.volume("cache")
        harness.start(data, cache)
        with httpx.Client(
            base_url=harness.base, headers={"Origin": harness.base}, timeout=4, trust_env=False
        ) as client:
            healthy(client)
            assert client.get("/api/health").json()["phase"] == "P5"
            wait_for(
                lambda: (
                    harness.docker(
                        "inspect", harness.name, "--format", "{{.State.Health.Status}}"
                    ).stdout.strip()
                    == "healthy"
                ),
                "docker_healthcheck_timeout",
            )
            checks.append("production_health_amd64")
            assert 'id="root"' in client.get("/").text
            checks.append("static_frontend")
            assert (
                harness.execute(
                    "python", "-c", "import os; print(os.getuid(),os.getgid())"
                ).stdout.strip()
                == "12001 12001"
            )
            assert (
                harness.execute(
                    "python",
                    "-c",
                    'from pathlib import Path; Path("/write-test").touch()',
                    check=False,
                ).returncode
                != 0
            )
            checks.append("custom_uid_gid_read_only_root")
            harness.execute(
                "python",
                "-c",
                (
                    "import importlib.util, shutil; "
                    'assert importlib.util.find_spec("pytest") is None; '
                    'assert shutil.which("uv") is None'
                ),
            )
            ffprobe = harness.execute("ffprobe", "-version").stdout.splitlines()[0]
            python = harness.execute("python", "--version").stdout.strip()
            harness.execute("ffmpeg", "-version")
            checks.append("runtime_media_tools_without_dev_dependencies")
            harness.execute("reeldock", "init-admin", "--password-stdin", input=password + "\n")
            login(client, password)
            remote_secret = secrets.token_urlsafe(24)
            config = {
                "webdav_url": "https://dav.example/dav/",
                "scrape_paths": ["/incoming"],
                "webdav_password": remote_secret,
            }
            saved = client.put("/api/config", json=config)
            assert saved.status_code == 200 and remote_secret not in saved.text
            checks.append("online_admin_and_encrypted_configuration")
            body = {"package_path": "/incoming/Generated", "idempotency_key": "persistent-pending"}
            one = client.post("/api/tasks", json=body)
            identifier = one.json()["task_id"]
            assert client.post("/api/tasks", json=body).json()["task_id"] == identifier
            assert client.post("/api/tasks/" + identifier + "/pause").status_code == 200
            wait_task(client, identifier, "paused")
            cursor = client.get("/api/events").json()["next_cursor"]
            key_hash = harness.execute(
                "python",
                "-c",
                (
                    "import hashlib; "
                    'print(hashlib.sha256(open("/data/master.key","rb").read()).hexdigest())'
                ),
            ).stdout
            harness.execute("reeldock", "backup", "--output", "/data/snapshot.tar.gz")
            checks.append("online_backup_with_key")
            assert harness.execute("reeldock", "migrate", check=False).returncode != 0
            checks.append("active_state_migration_rejected")
            harness.docker("stop", "--time", "35", harness.name)
            exit_code = int(
                harness.docker(
                    "inspect", harness.name, "--format", "{{.State.ExitCode}}"
                ).stdout.strip()
            )
            # Uvicorn re-raises SIGTERM after completing its graceful shutdown.
            assert exit_code in {0, 143}, "unexpected_shutdown_exit_code"
            harness.docker("start", harness.name)
            healthy(client)
            assert client.get("/api/config").json() == saved.json()
            assert client.get("/api/tasks/" + identifier).json()["status"] == "paused"
            assert client.get("/api/events").json()["next_cursor"] >= cursor
            assert (
                harness.execute(
                    "python",
                    "-c",
                    (
                        "import hashlib; "
                        'print(hashlib.sha256(open("/data/master.key","rb").read()).hexdigest())'
                    ),
                ).stdout
                == key_hash
            )
            checks.append("graceful_restart_configuration_key_session_tasks_events")
            harness.docker("stop", "--time", "35", harness.name)
            restored = harness.volume("restored")
            harness.docker(
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                "--user",
                harness.uid,
                "-v",
                restored + ":/data:nocopy",
                "-v",
                data + ":/snapshots:ro,nocopy",
                args.image,
                "restore",
                "--input",
                "/snapshots/snapshot.tar.gz",
            )
            empty_cache = harness.volume("empty-cache")
            harness.start(restored, empty_cache)
            healthy(client)
            assert client.get("/api/config").json() == saved.json()
            assert client.get("/api/tasks/" + identifier).json()["status"] == "paused"
            checks.append("restore_database_key_without_cache")
            harness.docker("stop", "--time", "35", harness.name)
            fixture_data, fixture_cache, remote = (
                harness.volume("fixture-data"),
                harness.volume("fixture-cache"),
                harness.volume("remote"),
            )
            harness.start(fixture_data, fixture_cache, remote)
            healthy(client)
            harness.execute("reeldock", "init-admin", "--password-stdin", input=password + "\n")
            client.cookies.clear()
            login(client, password)
            config.update(
                stable_seconds=1,
                scrape_actor_images=True,
                probe_timeout_seconds=90,
                probe_max_bytes=2097152,
            )
            assert client.put("/api/config", json=config).status_code == 200

            def scan(key):
                response = client.post("/api/scan", json={"idempotency_key": key})
                assert response.status_code == 202
                wait_task(client, response.json()["task_id"], "completed")

            scan("first-scan")
            time.sleep(1.1)
            scan("stable-scan")
            packages = client.get("/api/packages").json()["items"]
            assert len(packages) == 4 and all(p["scan_status"] == "stable" for p in packages)
            for name in ["ChineseMovie", "ChineseTV"]:
                job = submit(client, f"/incoming/{name} (2020)", name)
                wait_task(client, job, "completed")
            state = harness.state()
            assert state["calls"].get("media_reads", 0) == 0
            assert state["calls"].get("subtitle_downloads", 0) == 0
            assert state["calls"].get("move", 0) == 0
            checks.append("chinese_movie_and_series_zero_probe_or_video_reads")
            for package in client.get("/api/packages").json()["items"]:
                if package["original_language"] in {"zh", "cn"}:
                    assert all(item["probe_status"] == "not_started" for item in package["media"])
                    assert all(
                        item["subtitle_status"] == "skipped_tmdb_chinese"
                        for item in package["media"]
                    )
            bad = submit(client, "/incoming/BadMovie (2020)", "bad-image")
            wait_task(client, bad, "blocked", "base_assets_verified")
            state = harness.state()
            assert state["calls"].get("video-group:BadMovie (2020)", 0) == 0
            assert state["calls"].get("move-group:BadMovie (2020)", 0) == 0
            checks.append("required_image_failure_blocks_probe_and_completion")
            foreign = submit(client, "/incoming/ForeignTV (2020)", "foreign-series")
            failed = wait_task(client, foreign, "blocked", "subtitle_policy")
            assert failed["error_code"] == "subtitle_no_match"
            state = harness.state()
            assert state["calls"]["subtitle_downloads"] == 1
            assert state["calls"].get("move-group:ForeignTV (2020)", 0) == 0
            puts = state["calls"]["put-group:ForeignTV (2020)"]
            checks.append("real_ffprobe_and_generated_subtitle_download_partial_series_block")
            harness.execute(
                "python",
                "-c",
                (
                    "import shutil; from pathlib import Path; "
                    'shutil.rmtree("/cache/assets"); Path("/cache/assets").mkdir()'
                ),
            )
            harness.marker("repair-subtitles")
            harness.marker("crash-manifest", "ForeignTV (2020)")
            assert client.post("/api/tasks/" + foreign + "/retry").status_code == 200
            wait_for(
                lambda: (
                    harness.execute(
                        "python",
                        "-c",
                        (
                            "from pathlib import Path; "
                            'print(Path("/mock-remote/manifest-written").exists())'
                        ),
                    ).stdout.strip()
                    == "True"
                ),
                "manifest_marker_timeout",
            )
            harness.docker("kill", "--signal", "KILL", harness.name)
            harness.start(fixture_data, fixture_cache, remote)
            healthy(client)
            wait_task(client, foreign, "completed")
            state = harness.state()
            assert state["calls"].get("move", 0) == 0
            assert state["calls"]["subtitle_downloads"] == 2
            assert "/incoming/ForeignTV (2020)" in state["directories"]
            assert "/library/ForeignTV (2020)" not in state["directories"]
            # Two extra writes: the failed episode subtitle and final manifest.
            assert state["calls"]["put-group:ForeignTV (2020)"] == puts + 2
            checks.append("lost_manifest_response_container_kill_and_restart_reconciles_in_place")
            checks.append("retry_reuses_verified_base_and_successful_episode")
            checks.append("missing_cache_rebuilt_without_reuploading_base")
            harness.marker("repair-image")
            assert client.post("/api/tasks/" + bad + "/retry").status_code == 200
            wait_task(client, bad, "completed")
            checks.append("required_image_repair_recovers_package")
            events = client.get("/api/events?limit=200").json()["items"]
            assert any(event["code"] == "task_recovered" for event in events)
            checks.append("durable_recovery_events")
            exported = Path(args.output).parent / ("p5-kodi-fixtures-" + harness.suffix)
            exported.mkdir(parents=True, exist_ok=True)
            # Generated content only, for a separate Kodi import test / operator review.
            for path, encoded in harness.state()["files"].items():
                if path.startswith("/incoming/") and Path(path).suffix in {".nfo", ".jpg", ".srt"}:
                    relative = Path(path.removeprefix("/incoming/"))
                    category = "tv" if "TV" in relative.parts[0] else "movies"
                    target = exported / category / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    import base64

                    target.write_bytes(base64.b64decode(encoded))
            harness.docker(
                "cp", harness.name + ":/mock-remote/generated.mkv", str(exported / "generated.mkv")
            )
            import shutil

            for path in harness.state()["media"]:
                if path.startswith("/incoming/"):
                    relative = Path(path.removeprefix("/incoming/"))
                    category = "tv" if "TV" in relative.parts[0] else "movies"
                    target = exported / category / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(exported / "generated.mkv", target)
        return {
            "status": "passed",
            "image_architecture": architecture,
            "image_id": harness.docker(
                "image", "inspect", args.image, "--format", "{{.Id}}"
            ).stdout.strip(),
            "python": python,
            "ffprobe": ffprobe,
            "graceful_exit_code": exit_code,
            "scope": "local_container_with_mock_remote_services_real_ffprobe",
            "checks": checks,
            "real_nas": "unverified",
            "kodi": "unverified",
            "real_media_reads": 0,
            "real_media_moves": 0,
            "kodi_fixture_directory": str(exported.resolve()),
        }
    finally:
        harness.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="reeldock:0.1.0-local")
    parser.add_argument("--port", type=int, default=18005)
    parser.add_argument("--fixture-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("reports/p5-container.json"))
    args = parser.parse_args()
    checks = []
    try:
        result = run(args, checks)
    except Exception as error:
        result = {
            "status": "failed",
            "code": (str(error) or "assertion_failed")
            if isinstance(error, (AssertionError, RuntimeError))
            else type(error).__name__,
            "checks": checks,
            "real_nas": "unverified",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
