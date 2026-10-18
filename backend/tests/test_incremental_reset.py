import asyncio
import json
import time

import httpx
import pytest
from p2_support import jpeg
from p3_support import SRT, run, setup
from test_embedded_confirmation import blocked_embedded, confirm
from test_manual_subtitles import manual_app as manual_app

from reeldock.database import Database
from reeldock.domain import AppConfig, ConfigUpdate, ProviderError, RemoteEntry
from reeldock.models import Media
from reeldock.providers.webdav import WebDAVProvider
from reeldock.queue import Queue
from reeldock.runtime import Runtime
from reeldock.security import SettingsStore, Vault
from reeldock.worker import Worker


async def test_repeat_scrape_reuses_metadata_assets_subtitle_and_refills_only_missing_file(
    foundation, tmp_path
):
    env = await setup(foundation, tmp_path)
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    dav, metadata, probe, subtitles = env[3:7]
    counts = metadata.calls.copy(), probe.calls.copy(), subtitles.calls.copy(), dav.calls.copy()
    for cached in env[7].movies.cache.directory.iterdir():
        cached.unlink()
    task, _ = await run(env, "incremental-again")
    assert task.status == "completed", task.error_code
    assert metadata.calls == counts[0] and probe.calls == counts[1] and subtitles.calls == counts[2]
    assert {k: v for k, v in dav.calls.items() if k.startswith("put:")} == {
        k: v for k, v in counts[3].items() if k.startswith("put:")
    }
    del dav.files[package.remote_path + "/poster.jpg"]
    task, _ = await run(env, "refill-only-poster")
    assert task.status == "completed", task.error_code
    assert dav.calls["put:poster.jpg"] == counts[3]["put:poster.jpg"] + 1
    assert dav.calls["put:fanart.jpg"] == counts[3]["put:fanart.jpg"]
    assert probe.calls == counts[1] and subtitles.calls == counts[2]


async def test_existing_image_aliases_do_not_download_duplicate_pictures(foundation, tmp_path):
    env = await setup(foundation, tmp_path)
    dav, metadata = env[3:5]
    root = "/incoming/Example (2020)"
    dav.files[root + "/folder.jpg"] = jpeg()
    dav.files[root + "/fanart.png"] = jpeg("purple")
    metadata.bad |= {"poster", "fanart"}
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert metadata.calls["download:poster"] == metadata.calls["download:fanart"] == 0
    assert "folder.jpg" in package.base_required and "fanart.png" in package.base_required
    assert root + "/poster.jpg" not in dav.files and root + "/fanart.jpg" not in dav.files
    del dav.files[root + "/folder.jpg"]
    metadata.bad.clear()
    task, _ = await run(env, "replace-missing-alias")
    assert task.status == "completed", task.error_code
    assert root + "/poster.jpg" in dav.files


@pytest.mark.parametrize("change", ["missing_subtitle", "custom_sidecars"])
async def test_incremental_revalidates_changed_or_missing_sidecars(foundation, tmp_path, change):
    env = await setup(foundation, tmp_path)
    task, package = await run(env)
    assert task.status == "completed"
    dav, metadata, probe, subtitles = env[3:7]
    subtitle = package.remote_path + "/" + package.subtitle_evidence["path"]
    previous = metadata.calls.copy(), probe.calls.copy(), subtitles.calls.copy()
    if change == "missing_subtitle":
        del dav.files[subtitle]
    else:
        dav.files[subtitle] = SRT.replace(b"dialogue", b"customized dialogue")
        dav.files[package.remote_path + "/poster.jpg"] = jpeg("orange")
    task, _ = await run(env, "changed-sidecars")
    assert task.status == "completed", task.error_code
    assert probe.calls == previous[1]
    assert metadata.calls["download:poster"] == previous[0]["download:poster"]
    if change == "custom_sidecars":
        assert subtitles.calls == previous[2]
        assert dav.files[package.remote_path + "/poster.jpg"] == jpeg("orange")
    else:
        assert subtitles.calls["search"] == previous[2]["search"] + 1


async def test_marker_restores_embedded_result_into_fresh_database_without_probe(
    foundation, tmp_path
):
    env = await setup(foundation, tmp_path)
    env[5].data["streams"].append(
        {"index": 2, "type": "subtitle", "codec": "ass", "language": "chi", "title": "简英双语"}
    )
    task, _ = await run(env)
    assert task.status == "completed", task.error_code
    dav = env[3]
    marker = next(path for path in dav.files if "/embedded-" in path)
    stored = json.loads(dav.files[marker])
    assert stored["media"]["path"] == "Example.2020.1080p.mkv"
    assert stored["evidence"]["embedded"]["verification"] == "parsed_text"
    db = Database(tmp_path / "rebuilt" / "db.sqlite3")
    try:
        db.migrate()
        store = SettingsStore(db, Vault(tmp_path / "rebuilt"))
        store.save(ConfigUpdate(**env[1].load()[0].model_dump()))
        queue = Queue(db)
        worker = Worker(
            queue,
            store,
            Runtime(data_dir=tmp_path / "rebuilt"),
            storage_factory=dav,
            metadata_factory=env[4],
            probe=env[5],
            subtitle_factory=env[6],
        )
        config, revision = store.load()
        await worker.movies.scanner.scan(
            dav, config, revision, now=time.time() - config.stable_seconds - 1
        )
        await worker.movies.scanner.scan(dav, config, revision)
        previous = env[5].calls.copy()
        rebuilt = (db, store, queue, dav, env[4], env[5], env[6], worker, revision)
        task, package = await run(rebuilt, "new-database")
        assert task.status == "completed", task.error_code
        assert package.subtitle_status == "embedded_zh_hans"
        assert env[5].calls == previous and not env[6].calls
        assert sum("/embedded-" in path for path in dav.files) == 1
    finally:
        db.close()


async def confirmed_film(env):
    film = await blocked_embedded(env)
    accepted = confirm(env[0], film)
    assert accepted.status_code == 202
    await env[0].app.state.worker.execute(env[0].app.state.queue.claim("confirm-before-delete"))
    return env[0].get("/api/packages/" + film["id"]).json()


def preview(client, film):
    response = client.post(f"/api/packages/{film['id']}/metadata/preview")
    assert response.status_code == 200, response.text
    return response.json()


def delete_metadata(client, film, plan, key="delete-metadata"):
    return client.post(
        f"/api/packages/{film['id']}/metadata/delete",
        json={"preview_id": plan["preview_id"], "idempotency_key": key},
    )


async def test_delete_preview_preserves_videos_unrelated_files_and_allows_fresh_scrape(manual_app):
    client, dav, metadata, probe, _, _ = manual_app
    film = await confirmed_film(manual_app)
    root = film["path"]
    # A reset can adopt current settings without requiring an additional scan.
    config, revision = client.app.state.store.load()
    client.app.state.store.save(ConfigUpdate(**config.model_dump(), expected_revision=revision))
    unrelated = {
        root + "/notes.txt": b"keep",
        root + "/photos.jpg": jpeg(),
        root + "/Another.Movie.srt": SRT,
    }
    dav.files.update(unrelated)
    dav.files[root + "/Example.2020.1080p.assrt.123456789abc.srt"] = SRT
    video = dav.media.copy()
    plan = preview(client, film)
    assert plan["video_files_preserved"] == 1
    paths = [item["path"] for item in plan["files"]]
    assert any("embedded-" in path for path in paths)
    assert any(path.endswith(".nfo") for path in paths)
    assert any(path.endswith(".srt") for path in paths)
    assert all(
        not path.endswith(".mkv") and path not in {"notes.txt", "photos.jpg", "Another.Movie.srt"}
        for path in paths
    )
    accepted = delete_metadata(client, film, plan)
    assert accepted.status_code == 202, accepted.text
    assert delete_metadata(client, film, plan).json() == accepted.json()
    batch = client.post(
        "/api/batch/tasks", json={"package_ids": [film["id"]], "idempotency_key": "during-reset"}
    )
    assert batch.json()["items"][0]["accepted"] is False
    await client.app.state.worker.execute(client.app.state.queue.claim("delete-worker"))
    task = client.get("/api/tasks/" + accepted.json()["task_id"]).json()
    assert task["status"] == "completed", task
    assert dav.media == video and dav.files == unrelated
    current = client.get("/api/packages/" + film["id"]).json()
    assert current["tmdb_id"] is None and current["base_status"] == "pending"
    assert not current["assets"] and current["media"][0]["probe_evidence"] == {}
    assert current["context_version"] > film["context_version"]
    calls = metadata.calls.copy()
    submitted = client.post(
        "/api/batch/tasks",
        json={"package_ids": [film["id"]], "idempotency_key": "scrape-after-reset"},
    )
    assert submitted.json()["items"][0]["accepted"]
    await client.app.state.worker.execute(client.app.state.queue.claim("scrape-after-reset"))
    assert metadata.calls["download:poster"] == calls["download:poster"] + 1
    assert root + "/poster.jpg" in dav.files
    assert dav.media == video


async def test_delete_rejects_stale_files_and_invalid_preview_or_csrf(manual_app):
    client, dav, *_ = manual_app
    film = await confirmed_film(manual_app)
    plan = preview(client, film)
    endpoint = f"/api/packages/{film['id']}/metadata/delete"
    assert (
        client.post(
            endpoint,
            json={"preview_id": plan["preview_id"], "idempotency_key": "csrf"},
            headers={"X-CSRF-Token": "wrong"},
        ).status_code
        == 403
    )
    assert delete_metadata(client, film, {"preview_id": "not-present"}).status_code == 409
    changed = film["path"] + "/" + plan["files"][0]["path"]
    dav.files[changed] += b"changed after preview"
    before = dav.files.copy()
    response = delete_metadata(client, film, plan)
    await client.app.state.worker.execute(client.app.state.queue.claim("changed-file"))
    task = client.get("/api/tasks/" + response.json()["task_id"]).json()
    assert task["error_code"] == "metadata_changed_refresh_preview"
    assert dav.files == before


async def test_manual_marker_restores_confirmation_and_rejects_changed_source(manual_app):
    client, dav, _, probe, _, _ = manual_app
    film = await confirmed_film(manual_app)
    marker = next(path for path in dav.files if "/embedded-" in path)
    assert (
        json.loads(dav.files[marker])["evidence"]["embedded"]["verification"]
        == "manual_confirmation"
    )
    previous = probe.calls.copy()
    with client.app.state.db.sessions.begin() as session:
        media = session.get(Media, film["media"][0]["id"])
        media.probe_evidence, media.probe_status = {}, "not_started"
        media.subtitle_version, media.subtitle_status = None, "pending"
    result = client.post(
        "/api/batch/tasks", json={"package_ids": [film["id"]], "idempotency_key": "from-marker"}
    )
    await client.app.state.worker.execute(client.app.state.queue.claim("from-marker"))
    task = client.get("/api/tasks/" + result.json()["items"][0]["task_id"]).json()
    assert task["status"] == "completed", task
    restored = client.get("/api/packages/" + film["id"]).json()
    assert restored["subtitle_evidence"]["manual"] is True
    assert probe.calls == previous
    dav.media[next(iter(dav.media))] += 1
    config, revision = client.app.state.store.load()
    await client.app.state.worker.movies.scanner.scan(dav, config, revision, now=time.time() - 2)
    await client.app.state.worker.movies.scanner.scan(dav, config, revision)
    result = client.post(
        "/api/batch/tasks", json={"package_ids": [film["id"]], "idempotency_key": "changed-source"}
    )
    await client.app.state.worker.execute(client.app.state.queue.claim("changed-source"))
    assert probe.calls["probe"] == previous["probe"] + 1
    task = client.get("/api/tasks/" + result.json()["items"][0]["task_id"]).json()
    assert task["error_code"] == "default_audio_unknown"


@pytest.mark.parametrize("manual_app", ["tv"], indirect=True)
async def test_series_reset_deletes_episode_metadata_and_preserves_all_videos(manual_app):
    client, dav, *_ = manual_app
    film = await confirmed_film(manual_app)
    videos = dav.media.copy()
    plan = preview(client, film)
    assert plan["video_files_preserved"] == 2
    assert any(item["path"].endswith("S01E02.nfo") for item in plan["files"])
    response = delete_metadata(client, film, plan)
    await client.app.state.worker.execute(client.app.state.queue.claim("delete-series"))
    task = client.get("/api/tasks/" + response.json()["task_id"]).json()
    assert task["status"] == "completed", task
    assert dav.media == videos and not dav.files
    result = client.get("/api/packages/" + film["id"]).json()
    assert all(
        item["probe_status"] == "not_started" and item["subtitle_status"] == "pending"
        for item in result["media"]
    )


@pytest.mark.parametrize("failure", ["timeout", "crash"])
async def test_delete_recovers_after_remote_delete_and_lost_response(manual_app, failure):
    client, dav, *_ = manual_app
    film = await confirmed_film(manual_app)
    plan = preview(client, film)
    original = dav.delete
    interrupted = False

    async def failing_delete(path, **kwargs):
        nonlocal interrupted
        await original(path, **kwargs)
        if not interrupted:
            interrupted = True
            if failure == "crash":
                raise asyncio.CancelledError()
            raise ProviderError("storage_timeout", retryable=True)

    dav.delete = failing_delete
    response = delete_metadata(client, film, plan)
    await client.app.state.worker.execute(client.app.state.queue.claim("delete-interruption"))
    if failure == "crash":
        assert client.get("/api/tasks/" + response.json()["task_id"]).json()["status"] == "queued"
        dav.delete = original
        await client.app.state.worker.execute(client.app.state.queue.claim("delete-recovery"))
    task = client.get("/api/tasks/" + response.json()["task_id"]).json()
    assert task["status"] == "completed", task
    assert len(dav.media) == 1 and not dav.files
    assert all(value == 1 for key, value in dav.calls.items() if key.startswith("delete:"))


@pytest.mark.parametrize(
    "status,code",
    [(204, None), (412, "metadata_changed_refresh_preview"), (302, "storage_redirect_rejected")],
)
async def test_webdav_metadata_delete_uses_conditional_request_and_rejects_redirect(status, code):
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://evil.example/delete"})

    config = AppConfig(webdav_url="https://dav.example/dav/", scrape_paths=["/input"])
    async with WebDAVProvider(config, transport=httpx.MockTransport(transport)) as provider:

        async def stat(path):
            return RemoteEntry(path=path, is_dir=False, size=1, etag='"v1"')

        provider.stat = stat
        if code:
            with pytest.raises(ProviderError, match=code):
                await provider.delete("/input/movie.nfo", etag='"v1"')
        else:
            await provider.delete("/input/movie.nfo", etag='"v1"')
        with pytest.raises(ProviderError, match="metadata_delete_rejected"):
            await provider.delete("/input/movie.mkv")
    assert len(calls) == 1 and calls[0].method == "DELETE"
    assert calls[0].headers["If-Match"] == '"v1"'
