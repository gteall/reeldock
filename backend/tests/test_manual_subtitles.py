import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from p2_support import FakeTMDB
from p3_support import DAV, SRT, Probe, Shooter
from p4_support import TVMetadata

from reeldock.app import create_app
from reeldock.domain import AppConfig, ProviderError
from reeldock.models import Configuration, Package
from reeldock.providers.assrt import AssrtProvider
from reeldock.security import init_admin


class ManualProvider:
    def __init__(self):
        self.keywords, self.downloads = [], []
        self.body = SRT

    def __call__(self, config):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def search(self, keyword):
        self.keywords.append(keyword)
        return [
            {
                "id": identifier,
                "title": "Example " + identifier,
                "release": "Example.2020",
                "language": "简英双语",
                "format": "SRT",
                "source": "assrt",
            }
            for identifier in ["100001", "100002"]
        ]

    async def files(self, identifier):
        return [
            {
                "index": index,
                "name": name,
                "format": "srt",
                "url": "https://file0.assrt.net/download?private=never-expose",
            }
            for index, name in enumerate(["simplified.srt", "bilingual.srt"])
        ]

    async def download(self, identifier, index, name):
        self.downloads.append((identifier, index, name))
        return self.body


@pytest.fixture
def manual_app(runtime, config_body, request):
    dav, metadata, manual = DAV(), FakeTMDB(), ManualProvider()
    if getattr(request, "param", None) == "tv":
        metadata = TVMetadata("en")
        root = "/incoming/Example (2020)"
        dav.directories.add(root + "/Season 01")
        dav.media = {
            root + f"/Season 01/Example.S01E{episode:02d}.mkv": 1024**3 for episode in (1, 2)
        }
    probe, shooter = Probe(dav), Shooter(dav)
    shooter.empty = True
    app = create_app(
        runtime,
        storage_factory=dav,
        metadata_factory=metadata,
        probe=probe,
        subtitle_factory=shooter,
        manual_subtitle_factory=manual,
    )
    with TestClient(app) as client:
        init_admin(app.state.db, "admin", "manual-subtitle-password")
        login = client.post(
            "/api/auth/login",
            headers={"Origin": "http://localhost:8000"},
            json={"username": "admin", "password": "manual-subtitle-password"},
        )
        client.headers.update(
            {"Origin": "http://localhost:8000", "X-CSRF-Token": login.json()["csrf_token"]}
        )
        assert (
            client.put("/api/config", json={**config_body, "stable_seconds": 1}).status_code == 200
        )
        yield client, dav, metadata, probe, shooter, manual


async def failed_movie(env):
    client = env[0]
    worker = client.app.state.worker
    config, revision = client.app.state.store.load()
    await worker.movies.scanner.scan(env[1], config, revision, now=time.time() - 2)
    await worker.movies.scanner.scan(env[1], config, revision)
    task = client.post(
        "/api/tasks",
        json={
            "kind": "package_pipeline",
            "package_path": "/incoming/Example (2020)",
            "idempotency_key": "automatic",
        },
    ).json()["task_id"]
    await worker.execute(client.app.state.queue.claim("automatic-worker"))
    assert client.get("/api/tasks/" + task).json()["error_code"] == "subtitle_no_match"
    return client.get("/api/packages").json()["items"][0]


def configure_token(client, config_body):
    revision = client.get("/api/config").json()["revision"]
    result = client.put(
        "/api/config",
        json={
            **config_body,
            "stable_seconds": 1,
            "assrt_token": "private-assrt-token",
            "expected_revision": revision,
        },
    )
    assert result.status_code == 200
    assert result.json()["assrt_token_set"]
    assert "private-assrt" not in result.text


def search_files(client, film, keyword=None):
    base = f"/api/packages/{film['id']}/subtitles"
    body = {"media_id": film["media"][0]["id"], "expected_context_version": film["context_version"]}
    if keyword is not None:
        body["keyword"] = keyword
    result = client.post(base + "/search", json=body)
    assert result.status_code == 200, result.text
    search = result.json()
    files = client.post(
        base + "/files", json={"search_id": search["search_id"], "candidate_id": "100002"}
    )
    assert files.status_code == 200
    assert "never-expose" not in files.text and '"url"' not in files.text
    return (
        base,
        search,
        {
            "search_id": search["search_id"],
            "candidate_id": "100002",
            "file_index": 1,
            "idempotency_key": "apply-choice",
        },
    )


async def test_manual_search_keyword_choice_and_apply_after_automatic_failure(
    manual_app, config_body
):
    client, dav, metadata, probe, shooter, manual = manual_app
    film = await failed_movie(manual_app)
    # Adding only the search credential after a failed scrape must not require re-scraping.
    configure_token(client, config_body)
    base, search, _ = search_files(client, film)
    assert search["keyword"] == film["media"][0]["subtitle_search_keyword"] == "Example.2020.1080p"
    assert len(search["items"]) == 2
    base, search, apply = search_files(client, film, "Example BluRay 2020")
    assert manual.keywords == ["Example.2020.1080p", "Example BluRay 2020"]
    counts, shooter_counts = metadata.calls.copy(), shooter.calls.copy()
    puts = {key: value for key, value in dav.calls.items() if key.startswith("put:")}
    accepted = client.post(base + "/apply", json=apply)
    assert accepted.status_code == 202, accepted.text
    assert client.post(base + "/apply", json=apply).json() == accepted.json()
    task_id = accepted.json()["task_id"]
    await client.app.state.worker.execute(client.app.state.queue.claim("manual-worker"))
    task = client.get("/api/tasks/" + task_id).json()
    assert task["status"] == "completed", task
    assert manual.downloads == [("100002", 1, "bilingual.srt")]
    result = client.get("/api/packages/" + film["id"]).json()
    assert result["scrape_completed"] and result["subtitle_status"] == "downloaded_verified"
    assert result["media"][0]["subtitle_evidence"]["manual"] is True
    assert result["media"][0]["subtitle_evidence"]["keyword"] == "Example BluRay 2020"
    path = result["media"][0]["subtitle_evidence"]["path"]
    assert ".assrt." in path and dav.files[film["path"] + "/" + path]
    assert (
        probe.calls == {"probe": 1} and shooter.calls == shooter_counts and metadata.calls == counts
    )
    assert all(dav.calls[key] == value for key, value in puts.items())
    assert "never-expose" not in json.dumps(task)
    with client.app.state.db.sessions.begin() as session:
        assert "private-assrt-token" not in session.get(Configuration, 1).encrypted
        assert session.get(Package, film["id"]).context_version == film["context_version"]


async def test_manual_search_requires_token_origin_and_current_context(manual_app, config_body):
    client, _, _, _, _, manual = manual_app
    film = await failed_movie(manual_app)
    base = f"/api/packages/{film['id']}/subtitles"
    body = {"media_id": film["media"][0]["id"], "expected_context_version": film["context_version"]}
    assert client.post(base + "/search", json=body).json()["detail"] == "assrt_token_required"
    configure_token(client, config_body)
    assert (
        client.post(base + "/search", json=body, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert client.post(base + "/search", json={**body, "keyword": "ab"}).status_code == 422
    assert (
        client.post(base + "/search", json={**body, "expected_context_version": 999}).status_code
        == 409
    )
    assert not manual.keywords
    base, search, apply = search_files(client, film)
    assert (
        client.post(
            base + "/files", json={"search_id": search["search_id"], "candidate_id": "999999"}
        ).status_code
        == 404
    )
    assert client.post(base + "/apply", json={**apply, "file_index": 9}).status_code == 409
    with client.app.state.db.sessions.begin() as session:
        session.get(Package, film["id"]).context_version += 1
    assert client.post(base + "/apply", json=apply).status_code == 409
    assert not manual.downloads


async def test_invalid_manual_subtitle_does_not_upload_or_mark_complete(manual_app, config_body):
    client, dav, _, _, _, manual = manual_app
    film = await failed_movie(manual_app)
    configure_token(client, config_body)
    base, _, apply = search_files(client, film)
    manual.body = b"<html>failed download</html>"
    response = client.post(base + "/apply", json=apply)
    await client.app.state.worker.execute(client.app.state.queue.claim("bad-manual-subtitle"))
    task = client.get("/api/tasks/" + response.json()["task_id"]).json()
    assert task["status"] == "blocked" and task["error_code"] == "subtitle_not_text"
    assert not any(".assrt." in path for path in dav.files)
    assert not client.get("/api/packages/" + film["id"]).json()["scrape_completed"]


@pytest.mark.parametrize("manual_app", ["tv"], indirect=True)
async def test_manual_episode_application_preserves_other_episode_state(manual_app, config_body):
    client = manual_app[0]
    film = await failed_movie(manual_app)
    configure_token(client, config_body)
    assert len(film["media"]) == 2
    base, _, apply = search_files(client, film)
    first = client.post(base + "/apply", json=apply)
    await client.app.state.worker.execute(client.app.state.queue.claim("manual-first-episode"))
    assert client.get("/api/tasks/" + first.json()["task_id"]).json()["status"] == "completed"
    partial = client.get("/api/packages/" + film["id"]).json()
    assert partial["media"][0]["subtitle_status"] == "downloaded_verified"
    assert partial["media"][1]["subtitle_status"] == "failed"
    assert not partial["scrape_completed"]
    _, _, apply = search_files(client, {**partial, "media": [partial["media"][1]]})
    apply["idempotency_key"] = "apply-second-episode"
    second = client.post(base + "/apply", json=apply)
    await client.app.state.worker.execute(client.app.state.queue.claim("manual-second-episode"))
    task = client.get("/api/tasks/" + second.json()["task_id"]).json()
    assert task["status"] == "completed", task
    completed = client.get("/api/packages/" + film["id"]).json()
    assert completed["scrape_completed"]
    assert all(item["subtitle_status"] == "downloaded_verified" for item in completed["media"])
    assert len({item["subtitle_evidence"]["path"] for item in completed["media"]}) == 2


async def test_manual_apply_rejects_active_tasks_and_changed_sources(manual_app, config_body):
    client, dav, _, _, _, manual = manual_app
    film = await failed_movie(manual_app)
    configure_token(client, config_body)
    base, _, apply = search_files(client, film)
    queued = client.post(base + "/apply", json=apply)
    assert queued.status_code == 202
    assert (
        client.post(base + "/apply", json={**apply, "idempotency_key": "another-choice"}).json()[
            "detail"
        ]
        == "package_busy_pause_first"
    )
    dav.media[next(iter(dav.media))] += 1
    await client.app.state.worker.execute(client.app.state.queue.claim("source-changed"))
    task = client.get("/api/tasks/" + queued.json()["task_id"]).json()
    assert task["status"] == "blocked" and task["error_code"] == "source_changed_scan_again"
    assert not manual.downloads
    assert not any(".assrt." in path for path in dav.files)


async def test_config_blank_keeps_assrt_token_and_clear_removes_it(manual_app, config_body):
    client = manual_app[0]
    configure_token(client, config_body)
    revision = client.get("/api/config").json()["revision"]
    assert client.put(
        "/api/config", json={**config_body, "assrt_token": "", "expected_revision": revision}
    ).json()["assrt_token_set"]
    stored, revision = client.app.state.store.load()
    assert stored.assrt_token.get_secret_value() == "private-assrt-token"
    assert (
        client.put(
            "/api/config",
            json={**config_body, "clear_assrt_token": True, "expected_revision": revision},
        ).json()["assrt_token_set"]
        is False
    )


async def test_assrt_search_detail_download_protocol_and_credentials():
    calls = []

    def transport(request):
        calls.append(request)
        assert "cookie" not in request.headers
        if request.url.host == "api.assrt.net":
            assert request.headers["authorization"] == "Bearer private-assrt-token"
            assert "token=" not in str(request.url)
            if request.url.path.endswith("search"):
                assert request.url.params["q"] == "Example 2020"
                rows = [{"id": 100001, "native_name": "Example", "lang": {"desc": "简体"}}]
            else:
                rows = [
                    {
                        "id": 100001,
                        "filelist": [
                            {"f": "movie.zip", "url": "https://file0.assrt.net/archive.zip"},
                            {"f": "movie.srt", "url": "http://file0.assrt.net/subtitle.srt"},
                        ],
                    }
                ]
            return httpx.Response(200, json={"status": 0, "sub": {"subs": rows}})
        assert "authorization" not in request.headers
        assert request.url.scheme == "https"
        return httpx.Response(200, content=SRT)

    config = AppConfig(
        webdav_url="https://dav.example/dav/",
        scrape_paths=["/in"],
        assrt_token="private-assrt-token",
    )
    async with AssrtProvider(config, transport=httpx.MockTransport(transport)) as provider:
        assert (await provider.search("Example 2020"))[0]["id"] == "100001"
        files = await provider.files("100001")
        assert len(files) == 1 and files[0]["index"] == 1
        assert await provider.download("100001", 1, "movie.srt") == SRT
    assert len(calls) == 4


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/sub.srt",
        "https://assrt.net.evil.example/a",
        "https://user:pass@assrt.net/a",
        "http://assrt.net/a",
    ],
)
def test_assrt_download_host_restrictions(target):
    with pytest.raises(ProviderError, match="subtitle_host_not_allowed"):
        AssrtProvider.allowed(target)


@pytest.mark.parametrize(
    "status,code",
    [
        (20001, "assrt_token_invalid"),
        (30900, "assrt_rate_limited"),
        (101, "subtitle_keyword_too_short"),
    ],
)
async def test_assrt_error_statuses_are_safe(status, code):
    config = AppConfig(
        webdav_url="https://dav.example/dav/", scrape_paths=["/in"], assrt_token="private-token"
    )
    async with AssrtProvider(
        config,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"status": status, "message": "never-expose-private-response"}
            )
        ),
    ) as provider:
        with pytest.raises(ProviderError, match=code):
            await provider.search("Example")
