import json
import time

import pytest
from p3_support import SRT, run, setup
from sqlalchemy import select
from test_manual_subtitles import failed_movie
from test_manual_subtitles import manual_app as manual_app

from reeldock.models import Asset, Media, Package


@pytest.mark.parametrize("codec", ["ass", "hdmv_pgs_subtitle"])
@pytest.mark.parametrize("audio", ["unknown", "ambiguous"])
async def test_valid_embedded_subtitle_satisfies_policy_with_unresolved_audio(
    foundation, tmp_path, codec, audio
):
    env = await setup(foundation, tmp_path, audio="und" if audio == "unknown" else "eng")
    if audio == "ambiguous":
        env[5].data["streams"].append(
            {"index": 3, "type": "audio", "language": "chi", "default": 1}
        )
    env[5].data["streams"].append(
        {
            "index": 2,
            "type": "subtitle",
            "codec": codec,
            "language": "chi",
            "title": "简英双语",
            "default": 1,
        }
    )
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert package.subtitle_status == "embedded_zh_hans"
    assert package.subtitle_evidence["audio_error"] == f"default_audio_{audio}"
    assert "audio" not in package.subtitle_evidence
    assert not env[6].calls
    assert env[5].calls["extract"] == (1 if codec == "ass" else 0)


async def test_valid_external_subtitle_satisfies_policy_with_unknown_audio(foundation, tmp_path):
    env = await setup(foundation, tmp_path, audio="und")
    env[3].files["/incoming/Example (2020)/Example.2020.1080p.srt"] = SRT
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert package.subtitle_status == "external_verified"
    assert package.subtitle_evidence["audio_error"] == "default_audio_unknown"
    assert not env[6].calls


async def blocked_embedded(env):
    probe = env[3]
    probe.data["streams"][1]["language"] = "und"
    probe.data["streams"].extend(
        [
            {
                "index": 2,
                "type": "subtitle",
                "codec": "ass",
                "language": "chi",
                "title": "简英双语",
                "default": 1,
            },
            {
                "index": 3,
                "type": "subtitle",
                "codec": "hdmv_pgs_subtitle",
                "language": "chi",
                "title": "Chinese",
            },
        ]
    )
    # Conservative extraction cannot establish full dialogue. The user may have
    # watched the embedded track and can explicitly confirm it independently.
    probe.text = SRT.replace(b"00:01:30,000", b"00:00:01,000")
    return await failed_movie(env, "default_audio_unknown")


def confirm(client, film, *, media=None, index=2, key="confirm-embedded"):
    return client.post(
        f"/api/packages/{film['id']}/subtitles/confirm-embedded",
        json={
            "media_id": (media or film["media"][0])["id"],
            "expected_context_version": film["context_version"],
            "stream_index": index,
            "idempotency_key": key,
        },
    )


async def test_confirm_embedded_without_token_preserves_audio_and_survives_retry(manual_app):
    client, dav, metadata, probe, automatic, manual = manual_app
    film = await blocked_embedded(manual_app)
    assert not film["manual_subtitles_enabled"]
    counts = probe.calls.copy(), metadata.calls.copy(), dav.calls.copy()
    selected = confirm(client, film)
    assert selected.status_code == 202, selected.text
    assert confirm(client, film).json() == selected.json()
    await client.app.state.worker.execute(client.app.state.queue.claim("confirm-worker"))
    task = client.get("/api/tasks/" + selected.json()["task_id"]).json()
    assert task["status"] == "completed", task
    result = client.get("/api/packages/" + film["id"]).json()
    assert result["scrape_completed"] and result["subtitle_status"] == "embedded_zh_hans"
    assert result["subtitle_reason"] is None
    assert result["subtitle_evidence"]["manual"] is True
    assert result["subtitle_evidence"]["stream_index"] == 2
    assert result["subtitle_evidence"]["embedded"]["verification"] == "manual_confirmation"
    assert result["probe_evidence"]["audio_error"] == "default_audio_unknown"
    assert "audio" not in result["probe_evidence"]
    assert probe.calls == counts[0] and metadata.calls == counts[1]
    assert not automatic.calls and not manual.downloads and not manual.keywords
    assert not any(".assrt." in path for path in dav.files)
    for key, count in counts[2].items():
        if key.startswith("put:"):
            assert dav.calls[key] == count
    manifests = [json.loads(value) for path, value in dav.files.items() if "/.reeldock/" in path]
    assert manifests[-1]["subtitle_evidence"]["stream_index"] == 2
    # A fresh full pipeline must reuse this source-bound manual choice.
    submitted = client.post(
        "/api/tasks",
        json={
            "kind": "package_pipeline",
            "package_path": film["path"],
            "idempotency_key": "after-manual-confirmation",
        },
    )
    assert submitted.status_code == 202
    await client.app.state.worker.execute(client.app.state.queue.claim("retry-worker"))
    assert client.get("/api/tasks/" + submitted.json()["task_id"]).json()["status"] == "completed"
    refreshed = client.get("/api/packages/" + film["id"]).json()
    assert refreshed["subtitle_evidence"]["manual"] is True
    assert refreshed["subtitle_evidence"]["stream_index"] == 2
    assert probe.calls == counts[0] and not automatic.calls
    dav.media[next(iter(dav.media))] += 1
    config, revision = client.app.state.store.load()
    await client.app.state.worker.movies.scanner.scan(dav, config, revision, now=time.time())
    changed = client.get("/api/packages/" + film["id"]).json()
    assert changed["context_version"] > film["context_version"]
    assert changed["subtitle_status"] == "pending"
    assert changed["media"][0]["subtitle_evidence"] == {}
    assert changed["media"][0]["probe_status"] == "not_started"


async def test_confirmation_rejects_invalid_track_csrf_and_changed_context(manual_app):
    client = manual_app[0]
    film = await blocked_embedded(manual_app)
    for index in (0, 1, 99):
        response = confirm(client, film, index=index)
        assert (
            response.status_code == 409
            and response.json()["detail"] == "embedded_subtitle_not_found"
        )
    body = {
        "media_id": film["media"][0]["id"],
        "stream_index": 2,
        "expected_context_version": film["context_version"],
        "idempotency_key": "invalid",
    }
    url = f"/api/packages/{film['id']}/subtitles/confirm-embedded"
    assert client.post(url, json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post(url, json={**body, "media_id": "not-present"}).status_code == 404
    assert client.post(url, json={**body, "expected_context_version": 999}).status_code == 409
    with client.app.state.db.sessions.begin() as session:
        session.get(Media, film["media"][0]["id"]).probe_evidence = {}
    response = confirm(client, film)
    assert response.status_code == 409 and response.json()["detail"] == "subtitle_probe_required"


async def test_confirmation_rejects_active_task_and_source_changed_before_execution(manual_app):
    client, dav, _, probe, automatic, manual = manual_app
    film = await blocked_embedded(manual_app)
    selected = confirm(client, film)
    assert selected.status_code == 202
    assert confirm(client, film, key="other-choice").json()["detail"] == "package_busy_pause_first"
    dav.media[next(iter(dav.media))] += 1
    await client.app.state.worker.execute(client.app.state.queue.claim("source-changed"))
    task = client.get("/api/tasks/" + selected.json()["task_id"]).json()
    assert task["status"] == "blocked" and task["error_code"] == "source_changed_scan_again"
    assert not automatic.calls and not manual.downloads
    with client.app.state.db.sessions.begin() as session:
        assert session.get(Package, film["id"]).manifest_version is None
        assert not session.scalar(select(Asset).where(Asset.kind == "manifest"))


async def test_confirmation_requires_verified_base_assets(manual_app):
    client = manual_app[0]
    film = await blocked_embedded(manual_app)
    with client.app.state.db.sessions.begin() as session:
        session.scalar(select(Asset).where(Asset.kind == "poster")).status = "failed"
    response = confirm(client, film)
    assert response.status_code == 409
    assert response.json()["detail"] == "base_assets_not_remote_verified"


@pytest.mark.parametrize("manual_app", ["tv"], indirect=True)
async def test_each_episode_confirmation_keeps_other_episode_pending_until_confirmed(manual_app):
    client, _, _, probe, automatic, manual = manual_app
    film = await blocked_embedded(manual_app)
    assert len(film["media"]) == 2
    counts = probe.calls.copy()
    selected = confirm(client, film)
    await client.app.state.worker.execute(client.app.state.queue.claim("episode-one"))
    assert client.get("/api/tasks/" + selected.json()["task_id"]).json()["status"] == "completed"
    partial = client.get("/api/packages/" + film["id"]).json()
    assert partial["media"][0]["subtitle_status"] == "embedded_zh_hans"
    assert partial["media"][1]["subtitle_status"] == "needs_review"
    assert not partial["scrape_completed"]
    second = confirm(client, partial, media=partial["media"][1], index=3, key="episode-two")
    await client.app.state.worker.execute(client.app.state.queue.claim("episode-two"))
    assert client.get("/api/tasks/" + second.json()["task_id"]).json()["status"] == "completed"
    result = client.get("/api/packages/" + film["id"]).json()
    assert result["scrape_completed"] and result["subtitle_status"] == "all_media_verified"
    assert [item["subtitle_evidence"]["stream_index"] for item in result["media"]] == [2, 3]
    assert probe.calls == counts and not automatic.calls and not manual.downloads
