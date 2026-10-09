import json
from collections import Counter

import pytest
from p3_support import SRT
from p4_support import ROOT, run_tv, setup_tv
from sqlalchemy import select

from reeldock.domain import ProviderError, Stage
from reeldock.episodes import parse_episode
from reeldock.exporter import read_nfo
from reeldock.models import Asset, Media, Package, Task


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


@pytest.mark.parametrize("language", ["zh", "cn"])
async def test_chinese_series_entire_package_zero_probe(foundation, tmp_path, language):
    env = await setup_tv(foundation, tmp_path, language)
    db, _, _, dav, metadata, probe, shooter, _, _ = env
    task, package = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert package.kind == "tv" and package.subtitle_status == "skipped_tmdb_chinese"
    assert not probe.calls and not shooter.calls and dav.calls["media_reads"] == 0
    assert dav.calls["move"] == 1
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
    assert dav.calls["move"] == 1


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
    assert dav.calls["move"] == 1


async def test_existing_target_series_is_never_merged(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path)
    dav = env[3]
    dav.directories.add("/library/Example (2020)")
    dav.files["/library/Example (2020)/user.txt"] = b"keep"
    task, _ = await run_tv(env)
    assert task.error_code == "archive_target_conflict"
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


async def test_enhancement_http_failure_is_independent_and_can_retry_after_archive(
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


async def test_episode_existing_nfo_progress_preserved_and_wrong_episode_blocks(
    foundation, tmp_path
):
    env = await setup_tv(foundation, tmp_path, "en")
    from reeldock.domain import Episode
    from reeldock.exporter import export_nfo

    episode = Episode(
        source="tmdb", external_id="1101", title="Manual", series_id="123", season=1, episode=1
    )
    path = ROOT + "/Season 01/Example.S01E01.nfo"
    body = export_nfo(episode, [], tag="episodedetails").replace(
        b"</episodedetails>",
        b"<playcount>7</playcount><resume><position>45</position></resume></episodedetails>",
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


async def test_series_move_crash_recovers_without_second_move(foundation, tmp_path):
    import time

    env = await setup_tv(foundation, tmp_path)
    db, _, queue, dav, _, probe, _, worker, revision = env
    dav.move_mode = "crash"
    task_id = queue.submit("package_pipeline", ROOT, "crash-tv", revision)
    claim = queue.claim("crashed")
    await worker.execute(claim)
    with db.sessions.begin() as session:
        session.get(Package, claim.package_id).lease_until = time.time() - 1
    queue.recover()
    await worker.execute(queue.claim("restarted"))
    with db.sessions.begin() as session:
        assert session.get(Task, task_id).status == "completed"
    assert dav.calls["move"] == 1 and not probe.calls


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


async def test_move_reconciles_changed_etags_for_fully_verified_enhancements(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path)
    dav = env[3]
    entry = dav.entry

    def moved_entry(path):
        value = entry(path)
        if path.startswith("/library/") and path.endswith(".jpg"):
            value.etag = "new-driver-metadata"
        return value

    dav.entry = moved_entry
    task, _ = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert dav.calls["move"] == 1 and not env[5].calls


async def test_older_manifest_uses_bound_pre_move_enhancement_evidence(
    foundation, tmp_path, monkeypatch
):
    import reeldock.completion as completion

    canonical = completion.canonical

    def legacy(value):
        if "inventory" in value:
            value = {
                **value,
                "assets": [
                    a for a in value["assets"] if a["kind"] not in {"thumb", "season_poster"}
                ],
            }
        return canonical(value)

    monkeypatch.setattr(completion, "canonical", legacy)
    env = await setup_tv(foundation, tmp_path)
    dav = env[3]
    entry = dav.entry

    def moved(path):
        value = entry(path)
        if path.startswith("/library/") and path.endswith(".jpg"):
            value.etag = "changed"
        return value

    dav.entry = moved
    task, _ = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert dav.calls["move"] == 1


async def test_changed_enhancement_bytes_are_never_excused_by_etag_relaxation(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path)
    dav = env[3]
    move = dav.move

    async def corrupt_after_move(source, target):
        await move(source, target)
        dav.files[target + "/season01-poster.jpg"] = b"corrupt"

    dav.move = corrupt_after_move
    task, package = await run_tv(env)
    assert task.status == "blocked" and package.archive_status == "move_unknown"
    assert dav.calls["move"] == 1 and not env[5].calls


async def test_pre_p4_movie_snapshot_can_finish_protected_archive(foundation, tmp_path):
    from p3_support import run, setup

    env = await setup(foundation, tmp_path, original="zh")
    db, _, _, _, _, _, _, worker, _ = env
    archive = worker.handlers["archive"]

    async def legacy(claim):
        with db.sessions.begin() as session:
            package = session.get(Package, claim.package_id)
            package.source_snapshot = {
                k: v for k, v in package.source_snapshot.items() if k != "kind"
            }
        return await archive(claim)

    worker.handlers["archive"] = legacy
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert package.archive_status == "archived"
