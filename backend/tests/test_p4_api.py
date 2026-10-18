import time
from contextlib import contextmanager
from pathlib import PurePosixPath

from fastapi.testclient import TestClient
from p3_support import DAV
from p4_support import ROOT, TVMetadata, TVProbe, TVShooter
from sqlalchemy import select

from reeldock.app import create_app
from reeldock.models import Asset, Media, MovieRecord, Package
from reeldock.security import init_admin


@contextmanager
def tv_app(runtime, config_body, files=None):
    dav = DAV()
    dav.media = {
        ROOT + "/" + name: 1024**3
        for name in (files or ["Season 01/Example.S01E01.mkv", "Season 01/unknown.mkv"])
    }
    for path in dav.media:
        dav.directories.update(
            str(p) for p in PurePosixPath(path).parents if str(p).startswith(ROOT)
        )
    metadata, probe, shooter = TVMetadata(), TVProbe(dav), TVShooter(dav)
    app = create_app(
        runtime,
        storage_factory=dav,
        metadata_factory=metadata,
        probe=probe,
        subtitle_factory=shooter,
    )
    with TestClient(app) as client:
        init_admin(app.state.db, "admin", "p4-test-password")
        result = client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost:8000"},
            json={"username": "admin", "password": "p4-test-password"},
        )
        client.headers.update(
            {"Origin": "http://localhost:8000", "X-CSRF-Token": result.json()["csrf_token"]}
        )
        assert (
            client.put(
                "/api/config", json={**config_body, "stable_seconds": 1, "move_verified": True}
            ).status_code
            == 200
        )
        yield app, client, dav, metadata, probe, shooter


async def scan(app, dav):
    config, revision = app.state.store.load()
    await app.state.worker.movies.scanner.scan(dav, config, revision, now=time.time() - 2)
    await app.state.worker.movies.scanner.scan(dav, config, revision)


async def test_manual_mapping_stale_duplicate_and_resume(runtime, config_body):
    with tv_app(runtime, config_body) as (app, client, dav, metadata, probe, shooter):
        await scan(app, dav)
        package = client.get("/api/packages").json()["items"][0]
        assert package["scan_status"] == "needs_review"
        unknown = next(m for m in package["media"] if m["episode"] is None)
        url = f"/api/packages/{package['id']}/episodes/{unknown['id']}"
        body = {"season": 1, "episode": 1, "expected_context_version": package["context_version"]}
        assert client.put(url, json=body).json()["detail"] == "duplicate_episode_mapping"
        body["episode"] = 2
        assert client.put(url, json=body, headers={"X-CSRF-Token": "bad"}).status_code == 403
        assert client.put(url, json=body).status_code == 200
        assert client.put(url, json=body).status_code == 409
        await scan(app, dav)
        mapped = client.get("/api/packages").json()["items"][0]
        assert mapped["scan_status"] == "stable"
        assert next(m for m in mapped["media"] if m["id"] == unknown["id"])["manual_mapping"]
        batch = {
            "package_ids": [package["id"], "missing", package["id"]],
            "idempotency_key": "batch-p4",
        }
        result = client.post("/api/batch/tasks", json=batch).json()["items"]
        assert len(result) == 2 and result[0]["accepted"] and not result[1]["accepted"]
        task_id = result[0]["task_id"]
        assert client.post("/api/batch/tasks", json=batch).json()["items"][0]["task_id"] == task_id
        body = {"task_ids": [task_id, "missing"], "action": "pause"}
        assert client.post("/api/batch/control", json=body).json()["items"] == [
            {"id": task_id, "accepted": True},
            {"id": "missing", "accepted": False, "error_code": "task_not_found"},
        ]
        assert app.state.queue.claim("paused") is None
        assert (
            client.post(
                "/api/batch/control", json={"task_ids": [task_id], "action": "resume"}
            ).status_code
            == 200
        )
        await app.state.worker.execute(app.state.queue.claim("api-tv"))
        view = client.get("/api/packages/" + package["id"]).json()
        assert view["archive_status"] == "archived" and not view["lease_active"]
        assert {m["subtitle_status"] for m in view["media"]} == {"skipped_tmdb_chinese"}
        assert all(m["probe_status"] == "not_started" for m in view["media"])
        for kind in ["nfo", "season_poster", "thumb"]:
            asset = next(a for a in view["assets"] if a["kind"] == kind)
            assert client.get(asset["preview"]).status_code == 200
        assert not probe.calls and not shooter.calls and not dav.calls["media_reads"]


async def test_multi_episode_cannot_be_forced_into_one(runtime, config_body):
    with tv_app(runtime, config_body, ["Season 01/Show.S01E01E02.mkv"]) as (app, client, dav, *_):
        await scan(app, dav)
        view = client.get("/api/packages").json()["items"][0]
        assert view["error_code"] == "multi_episode_file_unsupported"
        assert (
            client.put(
                f"/api/packages/{view['id']}/episodes/{view['media'][0]['id']}",
                json={
                    "season": 1,
                    "episode": 1,
                    "expected_context_version": view["context_version"],
                },
            ).json()["detail"]
            == "multi_episode_file_unsupported"
        )


async def test_legacy_season_package_is_regrouped_with_checkpoints_invalidated(
    runtime, config_body
):
    with tv_app(runtime, config_body) as (app, client, dav, *_):
        with app.state.db.sessions.begin() as session:
            old = Package(remote_path=ROOT + "/Season 01", kind="movie")
            session.add(old)
            session.flush()
            session.add(MovieRecord(package_id=old.id, title="Example", scan_status="needs_review"))
            media = Media(
                package_id=old.id,
                remote_path=ROOT + "/Season 01/Example.S01E01.mkv",
                subtitle_status="downloaded_verified",
                subtitle_version=1,
            )
            session.add(media)
            session.flush()
            asset = Asset(
                package_id=old.id,
                media_id=media.id,
                relative_path="old.nfo",
                kind="nfo",
                required=True,
            )
            session.add(asset)
            old_id = old.id
        await scan(app, dav)
        with app.state.db.sessions.begin() as session:
            assert session.get(MovieRecord, old_id).scan_status == "superseded"
            new = session.scalar(select(Package).where(Package.remote_path == ROOT))
            rows = list(session.scalars(select(Media).where(Media.package_id == new.id)))
            assert len(rows) == 2 and all(m.subtitle_version is None for m in rows)
            assert session.scalar(select(Asset)).media_id is None
