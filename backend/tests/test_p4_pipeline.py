import json
from collections import Counter

import pytest
from p3_support import SRT
from p4_support import ROOT, run_tv, setup_tv
from sqlalchemy import select

from reeldock.domain import ProviderError, Stage
from reeldock.episodes import parse_episode
from reeldock.exporter import read_nfo
from reeldock.models import Asset, Media, MovieRecord, Package, Task


@pytest.mark.parametrize(
    "path,pair",
    [
        ("Season 01/Show.S01E02.mkv", (1, 2)),
        ("第十二季/示例.第十二季第三集.mkv", (12, 3)),
        ("Specials/Show.S00E01.mkv", (0, 1)),
        ("Season 02/E03.mkv", (2, 3)),
        ("示例.第二集.mkv", (1, 2)),
        ("Show.1x02.mkv", (1, 2)),
    ],
)
def test_episode_parsing(path, pair):
    assert parse_episode(ROOT + "/" + path, ROOT) == pair


@pytest.mark.parametrize(
    "name,code",
    [
        ("Show.S01E01E02.mkv", "multi_episode_file_unsupported"),
        ("Show.S01E01-E02.mkv", "multi_episode_file_unsupported"),
        ("Show.S01E01-02.mkv", "multi_episode_file_unsupported"),
        ("第一至二集.mkv", "multi_episode_file_unsupported"),
        ("Season 02/Show.S01E01.mkv", "episode_number_conflict"),
        ("Season 01/unknown.mkv", "episode_number_unknown"),
    ],
)
def test_ambiguous_episode_is_not_flattened(name, code):
    with pytest.raises(ProviderError, match=code):
        parse_episode(ROOT + "/" + name, ROOT)


@pytest.mark.parametrize("year,automatic", [(2020, True), (2019, False)])
async def test_series_ambiguous_candidates_follow_confidence_threshold(
    foundation, tmp_path, monkeypatch, year, automatic
):
    env = await setup_tv(foundation, tmp_path)
    db, _, _, dav, metadata, probe, shooter, _, _ = env
    metadata.series = metadata.series.model_copy(
        update={"title": "Example One", "original_title": "Example One", "year": year}
    )
    runner_up = metadata.series.model_copy(
        update={"external_id": "456", "title": "Example Two", "original_title": "Example Two"}
    )

    async def search(query, kind, year=None):
        assert kind == "tv"
        return [runner_up, metadata.series]

    monkeypatch.setattr(metadata, "search", search)
    task, package = await run_tv(env)
    with db.sessions.begin() as session:
        record = session.get(MovieRecord, package.id)
        assert len(record.candidates) == 2
        assert record.candidates[0]["confidence"] == record.candidates[1]["confidence"]
        assert record.manual_id is None
        if automatic:
            assert task.status == "completed", task.error_code
            assert package.tmdb_id == "123" and record.match_status == "matched"
            assert record.candidates[0]["confidence"] > 0.8
            assert read_nfo(dav.files[ROOT + "/tvshow.nfo"], "tvshow").findtext("title") == (
                "Example One"
            )
        else:
            assert task.status == "blocked" and task.error_code == "tmdb_match_needs_review"
            assert package.tmdb_id is None and record.match_status == "needs_review"
            assert record.candidates[0]["confidence"] <= 0.8
            assert not dav.files
    assert not probe.calls and not shooter.calls and dav.calls["move"] == 0


@pytest.mark.parametrize(
    "marker",
    [
        "{tmdb-27205}",
        "{tmdbid-27205}",
        "{tmdb=27205}",
        "{tmdbid=27205}",
        "[tmdb-27205]",
        "[tmdbid-27205]",
        "[tmdb=27205]",
        "[tmdbid=27205]",
    ],
)
async def test_series_filename_tmdb_id_skips_title_search_and_overrides_manual_choice(
    foundation, tmp_path, marker
):
    env = await setup_tv(
        foundation,
        tmp_path,
        files=[f"Season 01/无关片名{marker}.S01E01.mp4", "Season 01/另一片名.S01E02.mkv"],
    )
    db, _, _, dav, metadata, _, _, _, _ = env
    metadata.series.external_id = "27205"
    with db.sessions.begin() as session:
        session.scalar(select(MovieRecord)).manual_id = "456"
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert package.tmdb_id == "27205"
    assert metadata.calls["search:tv"] == 0 and metadata.calls["series"] == 1
    assert read_nfo(dav.files[ROOT + "/tvshow.nfo"], "tvshow").findtext("tmdbid") == "27205"


async def test_conflicting_series_filename_tmdb_ids_require_review(foundation, tmp_path):
    env = await setup_tv(
        foundation,
        tmp_path,
        files=[
            "Season 01/Example{tmdb-123}.S01E01.mkv",
            "Season 01/Example[tmdbid=456].S01E02.mp4",
        ],
    )
    task, _ = await run_tv(env)
    assert task.error_code == "tmdb_id_conflict"
    assert env[4].calls["search:tv"] == env[4].calls["series"] == 0
    assert not env[3].files


async def test_series_embedded_simplified_pgs_skips_subtitle_downloads(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path, "en")
    db, _, _, dav, _, probe, shooter, _, _ = env
    probe.data["streams"].append(
        {
            "index": 2,
            "type": "subtitle",
            "codec": "hdmv_pgs_subtitle",
            "language": "chi",
            "title": "简英双语特效",
            "default": 1,
        }
    )
    shooter.empty = True
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert package.subtitle_status == "all_media_verified"
    with db.sessions.begin() as session:
        media = list(session.scalars(select(Media).where(Media.package_id == package.id)))
        assert len(media) == 2
        assert all(item.subtitle_status == "embedded_zh_hans" for item in media)
        assert all(
            item.subtitle_evidence["embedded"]["verification"] == "stream_metadata"
            for item in media
        )
    assert probe.calls["extract"] == 0 and not shooter.calls
    assert dav.calls["media_reads"] == 0


@pytest.mark.parametrize("language", ["zh", "cn"])
async def test_chinese_series_entire_package_zero_probe(foundation, tmp_path, language):
    env = await setup_tv(foundation, tmp_path, language)
    db, _, _, dav, metadata, probe, shooter, _, _ = env
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert package.kind == "tv" and package.subtitle_status == "skipped_tmdb_chinese"
    assert not probe.calls and not shooter.calls and dav.calls["media_reads"] == 0
    assert dav.calls["move"] == 0
    assert (
        read_nfo(dav.files[package.remote_path + "/tvshow.nfo"], "tvshow").find("streamdetails")
        is None
    )
    with db.sessions.begin() as session:
        media = list(session.scalars(select(Media).where(Media.package_id == package.id)))
        assets = list(session.scalars(select(Asset).where(Asset.package_id == package.id)))
    assert len(media) == 2
    for item in media:
        assert item.original_language == language and item.details["original_language"] == language
        assert item.subtitle_status == "skipped_tmdb_chinese" and item.probe_status == "not_started"
        nfo = read_nfo(dav.files[item.remote_path.replace(".mkv", ".nfo")], "episodedetails")
        assert nfo.findtext("tmdbid") == item.details["external_id"]
        assert nfo.findtext("season") == str(item.season) and nfo.findtext("episode") == str(
            item.episode
        )
    assert len([a for a in assets if a.kind == "actor" and a.required]) == 1
    assert len([a for a in assets if a.kind == "nfo" and a.required]) == 3
    assert all(not a.required for a in assets if a.kind in {"season_poster", "thumb"})
    assert metadata.calls["season:1"] == 1


async def test_shared_image_failure_blocks_every_episode_and_reuses_base(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path, "en")
    db, _, queue, dav, metadata, probe, shooter, worker, _ = env
    metadata.bad.add("poster")
    task, package = await run_tv(env)
    assert task.stage == Stage.BASE and task.status == "blocked"
    assert (
        not probe.calls and not shooter.calls and dav.calls["media_reads"] == dav.calls["move"] == 0
    )
    puts = Counter({k: v for k, v in dav.calls.items() if k.startswith("put:")})
    metadata.bad.clear()
    queue.control(task.id, "retry")
    await worker.execute(queue.claim("retry"))
    with db.sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
    assert all(dav.calls[k] == v for k, v in puts.items())
    assert dav.order.index("readback:poster.jpg") < dav.order.index("probe")
    assert dav.calls["move"] == 0


async def test_one_missing_subtitle_keeps_package_then_retry_skips_successful_episode(
    foundation, tmp_path
):
    env = await setup_tv(foundation, tmp_path, "en")
    db, _, queue, dav, metadata, probe, shooter, worker, _ = env
    shooter.missing = {2}
    task, package = await run_tv(env)
    assert task.stage == Stage.SUBTITLE and task.error_code == "subtitle_no_match"
    assert dav.calls["move"] == 0
    with db.sessions.begin() as session:
        media = list(session.scalars(select(Media).order_by(Media.episode)))
    assert [m.subtitle_status for m in media] == ["downloaded_verified", "failed"]
    puts = Counter({k: v for k, v in dav.calls.items() if k.startswith("put:")})
    before = metadata.calls.copy()
    dav.files[ROOT + "/Season 01/Example.S01E02.en.srt"] = SRT
    queue.control(task.id, "retry")
    await worker.execute(queue.claim("new-owner"))
    with db.sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
        assert session.get(Package, package.id).subtitle_status == "all_media_verified"
    assert metadata.calls == before and all(dav.calls[k] == v for k, v in puts.items())
    assert all(v == 1 for v in probe.media_calls.values())
    assert shooter.queries["Example.S01E01.mkv"] == shooter.queries["Example.S01E02.mkv"] == 1
    assert dav.calls["move"] == 0


async def test_existing_target_series_is_never_merged(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path)
    dav = env[3]
    dav.directories.add("/library/Example (2020)")
    dav.files["/library/Example (2020)/user.txt"] = b"keep"
    task, _ = await run_tv(env)
    assert task.status == "completed" and task.error_code is None
    assert dav.calls["move"] == 0 and dav.files["/library/Example (2020)/user.txt"] == b"keep"
    assert any(p.startswith(ROOT) for p in dav.media)


async def test_episode_metadata_failure_retry_only_missing_episode(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path)
    db, _, queue, _, metadata, _, _, worker, _ = env
    metadata.failed_episodes.add(2)
    task, _ = await run_tv(env)
    assert task.stage == Stage.MATCH and task.error_code == "tmdb_not_found"
    metadata.failed_episodes.clear()
    queue.control(task.id, "retry")
    await worker.execute(queue.claim("retry-metadata"))
    with db.sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
    assert metadata.calls["episode:1:1"] == 1 and metadata.calls["episode:1:2"] == 2
    assert metadata.calls["series"] == metadata.calls["season:1"] == 1


@pytest.mark.parametrize("original", [None, "xx", ""])
async def test_unknown_series_language_blocks_without_probe(foundation, tmp_path, original):
    env = await setup_tv(foundation, tmp_path, original)
    task, package = await run_tv(env)
    assert task.status == "blocked" and package.subtitle_status == "needs_review"
    assert not env[5].calls and not env[6].calls and not env[3].calls["media_reads"]
    assert not env[3].calls["move"]


async def test_all_episodes_and_shared_assets_read_back_before_first_probe(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path, "en")
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    order = env[3].order
    first = order.index("probe")
    for name in [
        "tvshow.nfo",
        "poster.jpg",
        "fanart.jpg",
        "Example.S01E01.nfo",
        "Example.S01E02.nfo",
    ]:
        assert order.index("readback:" + name) < first
    assert package.subtitle_status == "all_media_verified"
    manifest = json.loads(
        env[3].files[
            package.remote_path
            + "/"
            + next(
                path[len(package.remote_path) + 1 :]
                for path in env[3].files
                if path.startswith(package.remote_path + "/") and "manifest" in path
            )
        ]
    )
    assert len(manifest["media"]) == 2


async def test_enhancement_http_failure_is_independent_and_can_retry_after_completion(
    foundation, tmp_path
):
    env = await setup_tv(foundation, tmp_path)
    db, _, queue, dav, metadata, probe, shooter, worker, revision = env
    images = metadata.images

    async def fail(*args, **kwargs):
        if kwargs.get("episode") == 2:
            raise ProviderError("tmdb_timeout", retryable=True)
        return await images(*args, **kwargs)

    metadata.images = fail
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    with db.sessions.begin() as session:
        asset = session.scalar(select(Asset).where(Asset.kind == "thumb", Asset.status == "failed"))
    assert not asset.required and asset.error_code == "tmdb_timeout"
    assert not probe.calls and not shooter.calls
    before = Counter(dav.calls)
    metadata.images = images
    retry = queue.submit(
        "asset_retry",
        package.remote_path,
        "thumb-retry",
        revision,
        checkpoint={"asset_id": asset.id},
    )
    await worker.execute(queue.claim("enhancement-worker"))
    with db.sessions.begin() as session:
        assert session.get(Task, retry).status == "completed"
        assert session.get(Asset, asset.id).status == "remote_verified"
    assert dav.calls["move"] == before["move"]
    assert dav.calls["put:tvshow.nfo"] == before["put:tvshow.nfo"]


async def test_existing_series_nfo_without_tmdbid_is_preserved(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path)
    path = ROOT + "/tvshow.nfo"
    original = (
        b'<tvshow><title>Manual</title><uniqueid type="tmdb">123</uniqueid>'
        b"<playcount>7</playcount><custom>keep</custom></tvshow>"
    )
    env[3].files[path] = original
    task, _ = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert env[3].files[path] == original and env[3].calls["put:tvshow.nfo"] == 0
    assert read_nfo(env[3].files[path], "tvshow").find("tmdbid") is None
    assert env[4].calls["search:tv"] == 0


async def test_episode_existing_nfo_without_tmdbid_preserves_progress(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path, "en")
    from reeldock.domain import Episode
    from reeldock.exporter import export_nfo

    episode = Episode(
        source="tmdb", external_id="1101", title="Manual", series_id="123", season=1, episode=1
    )
    path = ROOT + "/Season 01/Example.S01E01.nfo"
    body = (
        export_nfo(episode, [], tag="episodedetails")
        .replace(b"<tmdbid>1101</tmdbid>", b"")
        .replace(
            b"</episodedetails>",
            b"<playcount>7</playcount><resume><position>45</position></resume></episodedetails>",
        )
    )
    env[3].files[path] = body
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert env[3].files[package.remote_path + "/Season 01/Example.S01E01.nfo"] == body
    assert env[3].calls["put:Example.S01E01.nfo"] == 0


@pytest.mark.parametrize("bad", ["actor", "episode"])
async def test_any_required_asset_failure_keeps_all_episodes_unprobed(foundation, tmp_path, bad):
    env = await setup_tv(foundation, tmp_path, "en")
    if bad == "actor":
        env[4].bad.add("actor")
    else:
        env[3].corrupt.add("Example.S01E02.nfo")
    task, _ = await run_tv(env)
    assert task.status == "blocked", task.error_code
    assert not env[5].calls and not env[6].calls and not env[3].calls["media_reads"]
    assert not env[3].calls["move"]


async def test_specials_and_chinese_seasons_share_one_package(foundation, tmp_path):
    env = await setup_tv(
        foundation,
        tmp_path,
        files=["Specials/Example.S00E01.mkv", "第二季/Example.第二季第三集.mkv"],
    )
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert package.kind == "tv" and not env[5].calls
    assert package.remote_path + "/season-specials-poster.jpg" in env[3].files
    assert package.remote_path + "/season02-poster.jpg" in env[3].files


async def test_wrong_episode_existing_nfo_blocks_all_without_overwrite(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path, "en")
    from reeldock.domain import Episode
    from reeldock.exporter import export_nfo

    episode = Episode(
        source="tmdb",
        external_id="1101",
        title="Wrong number",
        series_id="123",
        season=1,
        episode=2,
    )
    body = export_nfo(episode, [], tag="episodedetails")
    path = ROOT + "/Season 01/Example.S01E01.nfo"
    env[3].files[path] = body
    task, _ = await run_tv(env)
    assert task.status == "blocked" and task.error_code == "tmdb_episode_identity_conflict"
    assert env[3].files[path] == body and not env[5].calls and not env[6].calls


async def test_foreign_series_checks_actual_audio_per_episode(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path, "en")
    probe = env[5]
    original = probe.probe

    async def per_episode():
        data = await original()
        if "S01E01" in probe.path:
            data["streams"][1]["language"] = "chi"
        return data

    probe.probe = per_episode
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    with env[0].sessions.begin() as session:
        media = list(session.scalars(select(Media).order_by(Media.episode)))
    assert [m.subtitle_status for m in media] == ["default_audio_chinese", "downloaded_verified"]
    assert env[6].calls["search"] == 1 and probe.calls["probe"] == 2


async def test_new_episode_during_base_stage_invalidates_package_before_probe(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path, "en")
    dav = env[3]
    put = dav.put

    async def mutate(path, body):
        await put(path, body)
        if path.endswith("fanart.jpg"):
            dav.media[ROOT + "/Season 01/Example.S01E03.mkv"] = 1024**3

    dav.put = mutate
    task, _ = await run_tv(env)
    assert task.status == "blocked" and task.error_code == "source_changed_scan_again"
    assert not env[5].calls and not env[6].calls and not dav.calls["move"]


async def test_changed_enhancement_bytes_are_rejected_before_completing_manifest(
    foundation, tmp_path
):
    env = await setup_tv(foundation, tmp_path)
    worker = env[7]
    subtitle = worker.handlers[Stage.SUBTITLE]

    async def corrupt_after_subtitle(claim):
        result = await subtitle(claim)
        env[3].files[ROOT + "/season01-poster.jpg"] = b"corrupt"
        return result

    worker.handlers[Stage.SUBTITLE] = corrupt_after_subtitle
    task, _ = await run_tv(env)
    assert task.status == "blocked" and task.stage == Stage.MANIFEST
    assert task.error_code == "upload_verification_failed"
    assert not env[3].calls["move"] and not env[5].calls
