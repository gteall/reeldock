import json

import httpx
import pytest
from fastapi.testclient import TestClient
from p2_support import MemoryDAV
from p3_support import run, setup
from sqlalchemy import select

from reeldock.app import create_app
from reeldock.domain import AppConfig, ConfigUpdate, ProviderError, Stage
from reeldock.models import (
    Admin,
    ArchiveIntent,
    Asset,
    Configuration,
    MovieRecord,
    Package,
    Task,
    TaskStep,
)
from reeldock.providers.webdav import WebDAVProvider
from reeldock.security import init_admin, passwords


def test_eight_character_admin_password_can_log_in(runtime):
    app = create_app(runtime)
    with TestClient(app) as client:
        with pytest.raises(ValueError, match="至少 8 位"):
            init_admin(app.state.db, "admin", "1234567")
        init_admin(app.state.db, "admin", "pass1234")
        with app.state.db.sessions.begin() as session:
            admin = session.get(Admin, 1)
            assert admin.password_hash != "pass1234"
            assert passwords.verify("pass1234", admin.password_hash)
        response = client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost:8000"},
            json={"username": "admin", "password": "pass1234"},
        )
        assert response.status_code == 200


def test_config_defaults_to_multiple_directories_and_no_actor_images(client, config_body):
    body = {key: value for key, value in config_body.items() if key != "scrape_actor_images"}
    body["scrape_paths"] = ["/incoming/", "/电影", "/incoming"]
    response = client.put("/api/config", json=body)
    assert response.status_code == 200
    assert response.json()["scrape_paths"] == ["/incoming", "/电影"]
    assert response.json()["scrape_actor_images"] is False
    assert (
        not {"input_path", "output_path", "move_verified", "stable_seconds"}
        & response.json().keys()
    )


def test_encrypted_legacy_configuration_loads_without_archive(foundation):
    db, store, _ = foundation
    legacy = {
        "webdav_url": "https://dav.example/dav/",
        "input_path": "/incoming",
        "output_path": "/library",
        "move_verified": True,
        "webdav_password": "existing-password",
    }
    with db.sessions.begin() as session:
        session.get(Configuration, 1).encrypted = store.vault.cipher.encrypt(
            json.dumps(legacy).encode()
        ).decode()
    config, revision = store.load()
    assert config.scrape_paths == ["/incoming"]
    assert config.scrape_actor_images is False
    assert not hasattr(config, "move_verified")
    store.save(ConfigUpdate(**config.model_dump(), expected_revision=revision))
    assert store.load()[0].webdav_password.get_secret_value() == "existing-password"


async def test_scans_multiple_and_overlapping_roots_and_enforces_scope(client, config_body):
    roots = ["/incoming", "/library", "/incoming/Example (2020)"]
    assert client.put("/api/config", json={**config_body, "scrape_paths": roots}).status_code == 200
    app, dav = client.app, MemoryDAV()
    dav.directories.add("/library/Second")
    dav.media["/library/Second/Second.2024.mkv"] = 1024**3
    config, revision = app.state.store.load()
    assert config.scan_roots == ["/incoming", "/library"]
    result = await app.state.worker.movies.scanner.scan(dav, config, revision)
    assert result["packages"] == 2
    views = client.get("/api/packages").json()["items"]
    assert {item["path"] for item in views} == {"/incoming/Example (2020)", "/library/Second"}
    assert all(item["scan_status"] == "stable" for item in views)
    for index, item in enumerate(views):
        assert (
            client.post(
                "/api/tasks",
                json={
                    "kind": "package_pipeline",
                    "package_path": item["path"],
                    "idempotency_key": f"root-{index}",
                },
            ).status_code
            == 202
        )
    assert (
        client.post(
            "/api/tasks",
            json={"package_path": "/incoming-other/Film", "idempotency_key": "outside"},
        ).status_code
        == 422
    )


async def test_configured_movie_directory_itself_can_be_scraped(foundation):
    db, store, _ = foundation
    config, revision = store.load()
    store.save(
        ConfigUpdate(
            **{**config.model_dump(), "scrape_paths": ["/incoming/Example (2020)"]},
            expected_revision=revision,
        )
    )
    config, revision = store.load()
    dav = MemoryDAV()
    from reeldock.scanner import Scanner

    scanner = Scanner(db)
    await scanner.scan(dav, config, revision)
    with db.sessions.begin() as session:
        assert session.scalar(select(MovieRecord)).scan_status == "stable"


@pytest.mark.parametrize("enabled", [False, True])
async def test_complete_pipeline_preserves_paths_and_only_downloads_opted_in_avatars(
    foundation, tmp_path, enabled
):
    env = await setup(foundation, tmp_path, original="zh", scrape_actor_images=enabled)
    db, _, _, dav, metadata, _, _, _, _ = env
    videos, directories = dict(dav.media), set(dav.directories)
    task, package = await run(env)
    assert task.status == "completed" and task.stage == Stage.MANIFEST
    assert dav.media == videos and directories <= dav.directories
    assert package.remote_path == "/incoming/Example (2020)"
    assert not dav.calls["move"]
    assert metadata.calls["download:actor"] == int(enabled)
    assert (package.remote_path + "/.actors" in dav.directories) is enabled
    with db.sessions.begin() as session:
        steps = list(session.scalars(select(TaskStep).where(TaskStep.task_id == task.id)))
        assert {step.stage for step in steps} == {
            Stage.MATCH,
            Stage.BASE,
            Stage.SUBTITLE,
            Stage.MANIFEST,
        }
        assert all(step.status == "completed" for step in steps)
        assert session.scalar(select(ArchiveIntent)) is None
        record = session.get(MovieRecord, package.id)
        assert [person["name"] for person in record.cast] == ["Actor One", "No Image"]
        avatars = list(session.scalars(select(Asset).where(Asset.kind == "actor")))
        assert bool(avatars) is enabled


async def test_disabled_avatars_ignore_strict_missing_images_and_bad_actor_downloads(
    foundation, tmp_path
):
    env = await setup(
        foundation, tmp_path, original="zh", scrape_actor_images=False, actor_policy="strict"
    )
    env[4].bad.add("actor")
    task, _ = await run(env)
    assert task.status == "completed", task.error_code
    assert not env[4].calls["download:actor"]


async def test_legacy_archive_step_finishes_without_calling_even_an_injected_move_handler(
    foundation, tmp_path
):
    env = await setup(foundation, tmp_path, original="zh", scrape_actor_images=False)
    db, _, queue, dav, _, _, _, worker, _ = env
    task, package = await run(env)
    with db.sessions.begin() as session:
        old = session.get(Task, task.id)
        old.stage, old.status = Stage.ARCHIVE, "queued"
        session.add(TaskStep(task_id=task.id, stage=Stage.ARCHIVE))
        session.add(
            ArchiveIntent(
                package_id=package.id,
                task_id=task.id,
                source=package.remote_path,
                target="/library/Example (2020)",
                snapshot={},
                manifest_sha256="a" * 64,
                status="sending",
            )
        )

    async def forbidden(claim):
        pytest.fail("upgraded tasks must never invoke a MOVE handler")

    worker.handlers[Stage.ARCHIVE] = forbidden
    await worker.execute(queue.claim("upgraded"))
    with db.sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
        step = session.scalar(select(TaskStep).where(TaskStep.stage == Stage.ARCHIVE))
        assert step.status == "skipped" and step.checkpoint["archive_removed"]
    assert not dav.calls["move"] and package.remote_path in dav.directories


def test_disabled_actor_retry_is_rejected_by_api(client, config_body):
    assert (
        client.put("/api/config", json={**config_body, "scrape_actor_images": False}).status_code
        == 200
    )
    with client.app.state.db.sessions.begin() as session:
        package = Package(remote_path="/incoming/Film")
        session.add(package)
        session.flush()
        asset = Asset(package_id=package.id, relative_path=".actors/Actor.jpg", kind="actor")
        session.add(asset)
        session.flush()
        identifier = asset.id
    response = client.post(f"/api/assets/{identifier}/retry", json={"idempotency_key": "avatar"})
    assert response.status_code == 409 and response.json()["detail"] == "actor_images_disabled"


async def test_legacy_move_flag_cannot_cause_a_webdav_move_request():
    def forbidden(request):
        pytest.fail("MOVE must be rejected before issuing an HTTP request")

    config = AppConfig(
        webdav_url="https://dav.example/dav/",
        input_path="/in",
        output_path="/out",
        move_verified=True,
    )
    async with WebDAVProvider(config, transport=httpx.MockTransport(forbidden)) as storage:
        with pytest.raises(ProviderError, match="archive_removed"):
            await storage.move("/in/Film", "/out/Film")
        assert not (await storage.capabilities())["move_enabled"]
