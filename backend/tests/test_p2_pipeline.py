import time
from collections import Counter
from pathlib import PurePosixPath

import pytest
from p2_support import FakeTMDB, MemoryDAV, jpeg
from sqlalchemy import select

from reeldock.domain import ConfigUpdate, Movie, Person, ProviderError, Stage
from reeldock.exporter import nfo_ids, read_nfo, validate_image
from reeldock.matching import automatic_candidate, parse_name, rank_candidates
from reeldock.models import Asset, MovieRecord, Package, Task, TaskStep
from reeldock.queue import base_verified
from reeldock.runtime import Runtime
from reeldock.worker import Worker


async def setup_pipeline(foundation, tmp_path, **config_changes):
    db, store, queue = foundation
    config, revision = store.load()
    if config_changes:
        store.save(
            ConfigUpdate(**{**config.model_dump(), **config_changes}, expected_revision=revision)
        )
        config, revision = store.load()
    dav, tmdb = MemoryDAV(), FakeTMDB()
    forbidden = Counter()

    async def no_subtitle(claim):
        forbidden["subtitle"] += 1
        raise AssertionError("P2 cannot enter subtitle")

    worker = Worker(
        queue,
        store,
        Runtime(data_dir=tmp_path),
        storage_factory=dav,
        metadata_factory=tmdb,
        handlers={Stage.SUBTITLE: no_subtitle},
    )
    scanner = worker.movies.scanner
    await scanner.scan(dav, config, revision, now=time.time() - config.stable_seconds - 1)
    await scanner.scan(dav, config, revision)
    return db, store, queue, config, revision, dav, tmdb, worker, forbidden


async def run_movie(env, key="movie"):
    db, _, queue, _, revision, dav, _, worker, _ = env
    path = next(iter(dav.media))
    task_id = queue.submit("movie_base", str(PurePosixPath(path).parent), key, revision)
    claim = queue.claim("test-owner")
    assert claim
    await worker.execute(claim)
    with db.sessions.begin() as session:
        return session.get(Task, task_id), session.get(Package, claim.package_id)


def forbidden_calls(env):
    dav, _, _, forbidden = env[5:]
    assert dav.calls["media_reads"] == dav.calls["move"] == forbidden["subtitle"] == 0


async def test_complete_movie_stops_before_subtitle_and_reuses_existing_assets(
    foundation, tmp_path
):
    env = await setup_pipeline(foundation, tmp_path)
    task, package = await run_movie(env)
    db, _, _, _, _, dav, tmdb, _, _ = env
    assert task.status == "completed", task.error_code
    assert task.stage == Stage.BASE and package.original_language == "en"
    with db.sessions.begin() as session:
        assert base_verified(session, session.get(Package, package.id))
        assert session.get(Package, package.id).subtitle_status == "pending"
        assert {s.stage for s in session.scalars(select(TaskStep))} == {Stage.MATCH, Stage.BASE}
        assert (
            session.scalar(select(Asset).where(Asset.required.is_(False))).status
            == "source_no_image"
        )
    nfo = next(body for path, body in dav.files.items() if path.endswith(".nfo"))
    root = read_nfo(nfo)
    assert root.find("fileinfo") is None and root.find("actor/thumb") is None
    assert nfo_ids(nfo) == {"123"}
    before = tmdb.calls.copy()
    writes = {k: v for k, v in dav.calls.items() if k.startswith("put:")}
    task, _ = await run_movie(env, "second-task")
    assert task.status == "completed"
    assert tmdb.calls["download:poster"] == before["download:poster"]
    assert {k: v for k, v in dav.calls.items() if k.startswith("put:")} == writes
    forbidden_calls(env)


@pytest.mark.parametrize("failure", ["bad_image", "upload_mismatch", "actor_failure"])
async def test_partial_retry_reuses_verified_assets(foundation, tmp_path, failure):
    env = await setup_pipeline(foundation, tmp_path)
    db, _, queue, _, _, dav, tmdb, worker, _ = env
    if failure == "upload_mismatch":
        dav.corrupt.add("poster.jpg")
    else:
        tmdb.bad.add("actor" if failure == "actor_failure" else "poster")
    task, package = await run_movie(env)
    assert task.status == "blocked"
    assert task.error_code == (
        "upload_verification_failed" if failure == "upload_mismatch" else "invalid_image"
    )
    with db.sessions.begin() as session:
        assert not base_verified(session, session.get(Package, package.id))
    before = tmdb.calls.copy()
    tmdb.bad.clear()
    dav.corrupt.clear()
    if failure == "upload_mismatch":
        del dav.files[package.remote_path + "/poster.jpg"]  # user removes failed test artifact
    queue.control(task.id, "retry")
    # Reconstruct worker to exercise durable cache/checkpoints after restart.
    worker = Worker(
        queue, env[1], Runtime(data_dir=tmp_path), storage_factory=dav, metadata_factory=tmdb
    )
    await worker.execute(queue.claim("restarted"))
    with db.sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
    assert tmdb.calls["search"] == before["search"]
    assert tmdb.calls["download:fanart"] == before["download:fanart"] == 1
    forbidden_calls(env)


async def test_put_timeout_reconciles_without_second_put(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    env[5].timeout_after_put.add("poster.jpg")
    task, _ = await run_movie(env)
    assert task.status == "completed"
    assert env[5].calls["put:poster.jpg"] == 1
    forbidden_calls(env)


async def test_homonym_requires_manual_choice_and_does_not_write(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    env[6].movies.append(Movie(source="tmdb", external_id="456", title="Example", year=2020))
    task, package = await run_movie(env)
    assert task.error_code == "tmdb_match_needs_review"
    assert not env[5].files
    with env[0].sessions.begin() as session:
        record = session.get(MovieRecord, package.id)
        assert len(record.candidates) == 2
        record.manual_id = "123"
    env[2].control(task.id, "retry")
    await env[7].execute(env[2].claim("manual"))
    with env[0].sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
    forbidden_calls(env)


@pytest.mark.parametrize("identifier", ["123", "999", None])
async def test_existing_nfo_preserves_user_fields_and_progress(foundation, tmp_path, identifier):
    env = await setup_pipeline(foundation, tmp_path)
    dav, tmdb = env[5:7]
    id_xml = f'<uniqueid type="tmdb">{identifier}</uniqueid>' if identifier else ""
    original = (
        f"<movie><title>人工标题</title>{id_xml}<playcount>7</playcount>"
        "<resume><position>321</position><total>999</total></resume>"
        "<userrating>9</userrating><custom>do not edit</custom></movie>"
    ).encode()
    path = "/incoming/Example (2020)/movie.nfo"
    dav.files[path] = original
    task, package = await run_movie(env)
    if identifier == "123":
        assert task.status == "completed", task.error_code
        assert tmdb.calls["search"] == 0
    elif identifier == "999":
        assert task.error_code == "tmdb_not_found"
    else:
        assert task.error_code == "existing_nfo_id_missing"
    assert dav.files[path] == original and dav.calls["put:movie.nfo"] == 0
    assert env[7].movies.cache.get(env[7].movies.cache.put(original)) == original
    forbidden_calls(env)


async def test_filename_and_nfo_conflict_never_overwrites(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    dav = env[5]
    video = next(iter(dav.media))
    dav.media[video.replace("Example.2020", "Example.2020.{tmdb-123}")] = dav.media.pop(video)
    await env[7].movies.scanner.scan(dav, env[3], env[4], now=time.time() - 700)
    await env[7].movies.scanner.scan(dav, env[3], env[4])
    dav.files["/incoming/Example (2020)/movie.nfo"] = (
        b'<movie><title>X</title><uniqueid type="tmdb">456</uniqueid></movie>'
    )
    task, _ = await run_movie(env)
    assert task.error_code == "tmdb_id_conflict"
    assert env[6].calls["search"] == env[6].calls["details"] == 0
    forbidden_calls(env)


async def test_strict_actors_and_actor_name_collision(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path, actor_policy="strict")
    task, _ = await run_movie(env)
    assert task.error_code == "actor_source_missing"
    forbidden_calls(env)


async def test_actor_collision_does_not_silently_suffix(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    env[6].people.append(Person(source="tmdb", external_id="3", name="Actor_One"))
    task, _ = await run_movie(env)
    assert task.error_code == "actor_filename_collision"
    assert not env[5].files
    forbidden_calls(env)


async def test_scan_stability_download_markers_source_change_and_no_content_reads(
    foundation, tmp_path
):
    env = await setup_pipeline(foundation, tmp_path)
    db, _, _, config, revision, dav, _, worker, _ = env
    scanner = worker.movies.scanner
    video = next(iter(dav.media))
    dav.media[video] += 1
    await scanner.scan(dav, config, revision, now=100)
    with db.sessions.begin() as session:
        assert session.scalar(select(MovieRecord)).scan_status == "waiting_stable"
    await scanner.scan(dav, config, revision, now=100 + config.stable_seconds)
    with db.sessions.begin() as session:
        assert session.scalar(select(MovieRecord)).scan_status == "stable"
    dav.files[video + ".part"] = b""
    await scanner.scan(dav, config, revision)
    with db.sessions.begin() as session:
        assert session.scalar(select(MovieRecord)).error_code == "download_in_progress"
    forbidden_calls(env)


def test_name_year_parsing_and_wrong_year_not_auto_selected():
    parsed = parse_name("Movie.Name.1999.1080p.WEB-DL.{tmdb-42}.mkv")
    assert (parsed.title, parsed.year, parsed.explicit_id) == ("Movie Name", 1999, "42")
    assert parse_name("1917.mkv").year is None
    rows = rank_candidates(
        parsed, [Movie(source="tmdb", external_id="42", title="Movie Name", year=2020)]
    )
    assert automatic_candidate(rows) is None
    with pytest.raises(ProviderError, match="invalid_image"):
        validate_image(jpeg()[:100])


async def test_existing_images_are_validated_reused_without_tmdb_downloads(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    dav, tmdb = env[5:7]
    for path in ["poster.jpg", "fanart.jpg", ".actors/Actor_One.jpg"]:
        dav.files["/incoming/Example (2020)/" + path] = jpeg("red")
    dav.directories.add("/incoming/Example (2020)/.actors")
    task, _ = await run_movie(env)
    assert task.status == "completed"
    assert (
        tmdb.calls["download:poster"]
        == tmdb.calls["download:fanart"]
        == tmdb.calls["download:actor"]
        == 0
    )
    assert dav.calls["put:poster.jpg"] == dav.calls["put:fanart.jpg"] == 0
    forbidden_calls(env)


async def test_user_edited_generated_nfo_is_preserved_after_rescan(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    task, _ = await run_movie(env)
    assert task.status == "completed"
    dav = env[5]
    path = next(p for p in dav.files if p.endswith(".nfo"))
    edited = dav.files[path].replace(
        b"</movie>",
        b"<playcount>5</playcount><resume><position>42</position></resume><custom>mine</custom></movie>",
    )
    dav.files[path] = edited
    await env[7].movies.scanner.scan(dav, env[3], env[4])
    task, _ = await run_movie(env, "after-user-edit")
    assert task.status == "completed", task.error_code
    assert dav.files[path] == edited and dav.calls["put:" + PurePosixPath(path).name] == 1
    forbidden_calls(env)


async def test_source_changes_during_upload_invalidate_evidence(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    dav = env[5]
    original = dav.put

    async def change_source(path, content):
        await original(path, content)
        dav.media[next(iter(dav.media))] += 1

    dav.put = change_source
    task, package = await run_movie(env)
    assert task.error_code == "source_changed_scan_again"
    with env[0].sessions.begin() as session:
        assert not base_verified(session, session.get(Package, package.id))
        assert session.get(MovieRecord, package.id).scan_status == "waiting_stable"
    forbidden_calls(env)


async def test_unknown_original_language_stays_unknown_and_no_probe(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    env[6].movies[0].original_language = None
    task, package = await run_movie(env)
    assert task.status == "completed" and package.original_language is None
    nfo = next(b for p, b in env[5].files.items() if p.endswith(".nfo"))
    assert read_nfo(nfo).find("originallanguage") is None
    forbidden_calls(env)


async def test_metadata_language_change_commits_invalidation_and_allows_new_task(
    foundation, tmp_path
):
    env = await setup_pipeline(foundation, tmp_path)
    task, _ = await run_movie(env)
    assert task.status == "completed"
    env[6].movies[0].original_language = "zh"
    task, package = await run_movie(env, "language-changed")
    assert task.error_code == "metadata_changed_submit_new_task"
    with env[0].sessions.begin() as session:
        assert session.get(Package, package.id).original_language == "zh"
        assert not base_verified(session, session.get(Package, package.id))
    task, package = await run_movie(env, "new-language-context")
    assert task.status == "completed", task.error_code
    assert package.original_language == "zh"
    forbidden_calls(env)


async def test_restart_after_put_before_readback_reuses_planned_bytes(foundation, tmp_path):
    import asyncio

    env = await setup_pipeline(foundation, tmp_path)
    dav, tmdb = env[5:7]
    original = dav.put
    interrupted = False

    async def interrupt_after_put(path, content):
        nonlocal interrupted
        await original(path, content)
        if path.endswith("poster.jpg") and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()

    dav.put = interrupt_after_put
    task, _ = await run_movie(env)
    assert task.status == "queued"
    worker = Worker(
        env[2], env[1], Runtime(data_dir=tmp_path), storage_factory=dav, metadata_factory=tmdb
    )
    await worker.execute(env[2].claim("after-restart"))
    with env[0].sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
    assert tmdb.calls["download:poster"] == dav.calls["put:poster.jpg"] == 1
    forbidden_calls(env)


async def test_imdb_nfo_maps_to_tmdb_without_overwriting_progress(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    dav, tmdb = env[5:7]
    tmdb.movies[0].imdb_id = "tt1234567"

    async def find_movie(imdb_id):
        assert imdb_id == "tt1234567"
        return tmdb.movies[0]

    tmdb.find_movie = find_movie
    path = "/incoming/Example (2020)/movie.nfo"
    body = (
        b'<movie><title>Existing</title><uniqueid type="imdb">tt1234567</uniqueid>'
        b"<playcount>8</playcount></movie>"
    )
    dav.files[path] = body
    task, _ = await run_movie(env)
    assert task.status == "completed", task.error_code
    assert dav.files[path] == body and tmdb.calls["search"] == 0
    forbidden_calls(env)


async def test_timeout_without_remote_file_retries_cached_bytes(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    dav, tmdb = env[5:7]
    original, first = dav.put, True

    async def timeout_before_put(path, content):
        nonlocal first
        if path.endswith("poster.jpg") and first:
            first = False
            raise ProviderError("storage_timeout", retryable=True)
        await original(path, content)

    dav.put = timeout_before_put
    task, _ = await run_movie(env)
    assert task.status == "retry_wait" and task.error_code == "upload_not_confirmed"
    with env[0].sessions.begin() as session:
        session.get(Task, task.id).retry_at = 0
    await env[7].execute(env[2].claim("reconcile-retry"))
    with env[0].sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
    assert tmdb.calls["download:poster"] == tmdb.calls["download:fanart"] == 1
    forbidden_calls(env)


async def test_actor_scope_has_visible_unselected_entries(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path, actor_limit=1)
    task, package = await run_movie(env)
    assert task.status == "completed"
    with env[0].sessions.begin() as session:
        cast = session.get(MovieRecord, package.id).cast
        assert cast[0]["selected"] and not cast[1]["selected"]
        assert cast[1]["status"] == "not_selected"
    forbidden_calls(env)


async def test_previous_identity_artwork_is_never_rebound_to_new_film(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    task, package = await run_movie(env)
    assert task.status == "completed"
    # Simulate an explicit identity change after manually removing the conflicting NFO.
    dav = env[5]
    del dav.files[next(p for p in dav.files if p.endswith(".nfo"))]
    env[6].movies[0].external_id = "456"
    with env[0].sessions.begin() as session:
        from reeldock.queue import revise_package

        current = session.get(Package, package.id)
        revise_package(session, current, tmdb_id="456", original_language=None)
        current.base_required = []
        session.get(MovieRecord, package.id).manual_id = "456"
    task, _ = await run_movie(env, "new-film-identity")
    assert task.error_code == "existing_asset_identity_conflict"
    assert dav.calls["put:poster.jpg"] == 1
    forbidden_calls(env)


async def test_bad_explicit_id_package_does_not_abort_other_discovery(foundation, tmp_path):
    env = await setup_pipeline(foundation, tmp_path)
    dav = env[5]
    folder = "/incoming/Bad IDs"
    dav.directories.add(folder)
    dav.media[folder + "/Bad.{tmdb-123}.{tmdb-456}.mkv"] = 1234
    await env[7].movies.scanner.scan(dav, env[3], env[4])
    with env[0].sessions.begin() as session:
        rows = list(session.scalars(select(MovieRecord)))
        assert len(rows) == 2
        assert any(
            r.error_code == "tmdb_id_conflict" and r.scan_status == "needs_review" for r in rows
        )
    forbidden_calls(env)


async def test_changed_required_actor_plan_commits_invalidation_for_new_task(foundation, tmp_path):
    from reeldock.domain import Artwork

    env = await setup_pipeline(foundation, tmp_path)
    task, package = await run_movie(env)
    assert task.status == "completed"
    env[6].people.append(
        Person(
            source="tmdb",
            external_id="9",
            name="New Actor",
            profile=Artwork(source="tmdb", external_id="/new.jpg", kind="actor"),
        )
    )
    task, _ = await run_movie(env, "changed-plan")
    assert task.error_code == "asset_plan_changed_submit_new_task"
    with env[0].sessions.begin() as session:
        assert not base_verified(session, session.get(Package, package.id))
    task, _ = await run_movie(env, "new-plan-context")
    assert task.status == "completed", task.error_code
    assert env[6].calls["download:poster"] == 1
    assert env[6].calls["download:actor"] == 2
    forbidden_calls(env)
