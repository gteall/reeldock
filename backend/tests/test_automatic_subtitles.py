import json

import httpx
import pytest
from p3_support import SRT, run, setup
from p4_support import run_tv, setup_tv
from sqlalchemy import select

from reeldock.domain import ConfigUpdate
from reeldock.models import Configuration, Media
from reeldock.providers.assrt import AssrtProvider, episode_file_matches


class AssrtService:
    def __init__(self, *, series=False, empty=False, invalid_first=False):
        self.series, self.empty, self.invalid_first = series, empty, invalid_first
        self.requests, self.downloads = [], []

    def __call__(self, config):
        return AssrtProvider(config, transport=httpx.MockTransport(self.respond))

    def respond(self, request):
        self.requests.append(request)
        assert "cookie" not in request.headers
        assert request.url.host in {"api.assrt.net", "file0.assrt.net"}
        if request.url.host == "file0.assrt.net":
            assert "authorization" not in request.headers
            self.downloads.append(request.url.path)
            body = b"<html>not a subtitle</html>" if request.url.path == "/bad.srt" else SRT
            return httpx.Response(200, content=body)
        assert request.headers["authorization"] == "Bearer private-assrt-token"
        assert "token" not in request.url.params
        if request.url.path.endswith("/search"):
            assert request.url.params["no_muxer"] == "1"
            rows = [
                {"id": 100001, "native_name": "Example", "lang": {"desc": "English"}},
                {"id": 100002, "native_name": "Example", "lang": {"desc": "双语"}},
            ]
            if self.series:
                rows.insert(0, {"id": 100003, "native_name": "Example S02E24 简体"})
            if self.empty:
                rows = []
        else:
            identifier = request.url.params["id"]
            assert identifier != "100003", "wrong-season results must be skipped"
            files = ["Example.2020.eng.srt", "Example.2020.chs.srt"]
            if self.series:
                files = [f"Example.S01E{episode:02d}.chs.srt" for episode in range(1, 25)]
            if self.invalid_first:
                files.insert(0, "bad.chs.srt")
            rows = [
                {
                    "id": int(identifier),
                    "filelist": [
                        {
                            "f": name,
                            "url": "https://file0.assrt.net/"
                            + ("bad.srt" if name == "bad.chs.srt" else name),
                        }
                        for name in files
                    ],
                }
            ]
        return httpx.Response(200, json={"status": 0, "sub": {"subs": rows}})


@pytest.mark.parametrize("invalid_first", [False, True])
async def test_automatic_assrt_uses_saved_token_validates_and_writes_in_place(
    foundation, tmp_path, invalid_first
):
    env = await setup(foundation, tmp_path, assrt_token="private-assrt-token")
    service = AssrtService(invalid_first=invalid_first)
    env[7].completion.subtitle_factory = service
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert package.subtitle_status == "downloaded_verified"
    assert package.subtitle_evidence["source"] == "assrt"
    assert package.subtitle_evidence["keyword"] == "Example.2020.1080p"
    assert package.subtitle_evidence["candidate_id"] == "100002"
    assert package.subtitle_evidence["file_name"] == "Example.2020.chs.srt"
    assert package.subtitle_evidence["manual"] is False
    path = package.remote_path + "/" + package.subtitle_evidence["path"]
    assert ".assrt." in path and env[3].files[path]
    assert service.downloads[-1] == "/Example.2020.chs.srt"
    assert len(service.downloads) == (2 if invalid_first else 1)
    assert all(
        request.url.params["id"] == "100002"
        for request in service.requests
        if request.url.path.endswith("/detail")
    )
    assert env[3].calls["media_reads"] == env[3].calls["move"] == 0


async def test_automatic_episode_from_season_pack_after_first_ten_files(foundation, tmp_path):
    _, store, _ = foundation
    config, revision = store.load()
    store.save(
        ConfigUpdate(
            **{**config.model_dump(), "assrt_token": "private-assrt-token"},
            expected_revision=revision,
        )
    )
    env = await setup_tv(
        foundation, tmp_path, original="en", files=["Season 01/Example.S01E24.mkv"]
    )
    service = AssrtService(series=True)
    env[7].completion.subtitle_factory = service
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert service.requests[0].url.params["q"] == "Example.S01E24"
    assert service.downloads == ["/Example.S01E24.chs.srt"]
    with env[0].sessions.begin() as session:
        media = session.scalar(select(Media).where(Media.package_id == package.id))
        assert media.subtitle_evidence["file_name"] == "Example.S01E24.chs.srt"


@pytest.mark.parametrize("missing_token", [True, False])
async def test_automatic_missing_token_or_no_match_preserves_base_and_probe(
    foundation, tmp_path, missing_token
):
    changes = {} if missing_token else {"assrt_token": "private-assrt-token"}
    env = await setup(foundation, tmp_path, **changes)
    service = AssrtService(empty=True)
    env[7].completion.subtitle_factory = service
    task, package = await run(env)
    assert task.status == "blocked"
    assert task.error_code == ("assrt_token_required" if missing_token else "subtitle_no_match")
    assert package.base_status == "remote_verified"
    assert env[5].calls == {"probe": 1}
    assert len(service.requests) == (0 if missing_token else 1)
    assert not any(".assrt." in path for path in env[3].files)
    with env[0].sessions.begin() as session:
        media = session.scalar(select(Media).where(Media.package_id == package.id))
        assert media.probe_evidence["subtitle_search"] == {
            "source": "assrt",
            "keyword": "Example.2020.1080p",
        }


@pytest.mark.parametrize("chinese", [True, False])
async def test_assrt_token_not_required_when_subtitle_policy_already_satisfied(
    foundation, tmp_path, chinese
):
    env = await setup(foundation, tmp_path, original="zh" if chinese else "en")
    if not chinese:
        env[5].data["streams"].append(
            {
                "index": 2,
                "type": "subtitle",
                "codec": "hdmv_pgs_subtitle",
                "language": "chi",
                "title": "简英双语",
                "default": 1,
            }
        )
    service = AssrtService()
    env[7].completion.subtitle_factory = service
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert package.subtitle_status == ("skipped_tmdb_chinese" if chinese else "embedded_zh_hans")
    assert not service.requests


@pytest.mark.parametrize(
    "name,description,expected",
    [
        ("Example.S01E02.chs.srt", "Example S01 complete", True),
        ("Example.S02E02.chs.srt", "Example", False),
        ("Example.S01E03.chs.srt", "Example", False),
        ("Example.S01E02E03.srt", "Example", False),
        ("简体.srt", "Example S01E02", True),
        ("简体.srt", "Example S01E02-E08", False),
        ("简体.srt", "Example S01 complete", False),
        ("Season 02/E02.srt", "Example", False),
        ("E02.srt", "Example", True),
    ],
)
def test_automatic_episode_files_require_matching_season_and_episode(name, description, expected):
    assert episode_file_matches(name, description, {"season": 1, "episode": 2}) is expected


def test_legacy_download_hosts_are_ignored_and_saved_token_survives(foundation):
    db, store, _ = foundation
    config, _ = store.load()
    legacy = {
        **config.model_dump(mode="json"),
        "subtitle_download_hosts": ["shooter.cn", "www.shooter.cn"],
        "assrt_token": "private-assrt-token",
    }
    with db.sessions.begin() as session:
        session.get(Configuration, 1).encrypted = store.vault.cipher.encrypt(
            json.dumps(legacy).encode()
        ).decode()
    loaded, revision = store.load()
    assert loaded.assrt_token.get_secret_value() == "private-assrt-token"
    assert "subtitle_download_hosts" not in store.public()
    assert store.public()["assrt_token_set"]
    assert "private-assrt-token" not in json.dumps(store.public())
    store.save(ConfigUpdate(**loaded.model_dump(), expected_revision=revision))
    assert store.load()[0].assrt_token.get_secret_value() == "private-assrt-token"
