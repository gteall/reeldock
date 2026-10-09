import json

import pytest
from p3_support import SRT, run, setup
from sqlalchemy import select

from reeldock.domain import Stage
from reeldock.models import ArchiveIntent, Asset, MovieRecord, Package, Task


@pytest.mark.parametrize("code", ["zh", "cn"])
async def test_chinese_zero_calls_and_archives(foundation, tmp_path, code):
    env = await setup(foundation, tmp_path, original=code)
    db, _, _, dav, tmdb, probe, shooter, _, _ = env
    dav.files["/incoming/Example (2020)/Example.2020.1080p.srt"] = b"<html>broken</html>"
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert package.subtitle_status == "skipped_tmdb_chinese"
    assert package.archive_status == "archived"
    assert probe.calls == shooter.calls == {}
    assert dav.calls["media_reads"] == dav.calls["get:Example.2020.1080p.srt"] == 0
    assert dav.calls["move"] == 1
    with db.sessions.begin() as session:
        assert session.get(MovieRecord, package.id).scan_status == "archived"
        manifest = session.scalar(
            select(Asset).where(Asset.kind == "manifest", Asset.required.is_(True))
        )
    parsed = json.loads(dav.files[package.remote_path + "/" + manifest.relative_path])
    assert parsed["package_id"] == package.id
    assert parsed["subtitle_evidence"]["actual_audio"] == "not_probed"


async def test_all_base_readbacks_precede_first_probe_and_shooter(foundation, tmp_path):
    env = await setup(foundation, tmp_path)
    _, _, _, dav, _, probe, shooter, _, _ = env
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    first = dav.order.index("probe")
    for name in ["poster.jpg", "fanart.jpg", "Actor_One.jpg", "Example.2020.1080p.nfo"]:
        assert dav.order.index("readback:" + name) < first
    assert (
        dav.order.index("probe")
        < dav.order.index("range")
        < dav.order.index("shooter")
        < dav.order.index("move")
    )
    assert package.subtitle_status == "downloaded_verified"
    assert dav.calls["media_reads"] == 4
    assert shooter.calls == {"search": 1, "download": 1}


@pytest.mark.parametrize("bad", ["poster", "fanart", "actor"])
async def test_base_failure_zero_calls(foundation, tmp_path, bad):
    env = await setup(foundation, tmp_path)
    _, _, _, dav, tmdb, probe, shooter, _, _ = env
    tmdb.bad.add(bad)
    task, _ = await run(env)
    assert task.stage == Stage.BASE and task.status == "blocked"
    assert not probe.calls and not shooter.calls
    assert dav.calls["media_reads"] == dav.calls["move"] == 0


@pytest.mark.parametrize("original", [None, "", "???", "xx", "und", "en-US"])
async def test_unknown_original_needs_review_without_probe(foundation, tmp_path, original):
    env = await setup(foundation, tmp_path, original=original)
    task, package = await run(env)
    assert task.error_code == "tmdb_original_language_unknown"
    assert package.subtitle_status == "needs_review"
    assert not env[5].calls and not env[6].calls
    assert env[3].calls["media_reads"] == env[3].calls["move"] == 0


@pytest.mark.parametrize("audio", ["chi", "zho", "cmn", "yue", "zh"])
async def test_default_chinese_exempts(foundation, tmp_path, audio):
    env = await setup(foundation, tmp_path, audio=audio)
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert package.subtitle_status == "default_audio_chinese"
    assert not env[6].calls and env[3].calls["media_reads"] == 0


@pytest.mark.parametrize("variant", ["unknown", "multiple", "nondefault", "commentary"])
async def test_audio_selection(foundation, tmp_path, variant):
    env = await setup(foundation, tmp_path)
    streams = env[5].data["streams"]
    if variant == "unknown":
        streams[1]["language"] = "und"
    elif variant == "commentary":
        streams[1]["title"] = "Director Commentary"
    else:
        streams.append(
            {"index": 2, "type": "audio", "language": "chi", "default": int(variant == "multiple")}
        )
    task, package = await run(env)
    if variant == "nondefault":
        assert task.status == "completed" and package.subtitle_status == "downloaded_verified"
    else:
        assert package.subtitle_status == "needs_review"
        assert not env[6].calls and env[3].calls["move"] == 0


@pytest.mark.parametrize(
    "variant", ["hans", "forced", "hant", "chi", "incomplete", "bitmap", "conflict"]
)
async def test_embedded_subtitle_matrix(foundation, tmp_path, variant):
    env = await setup(foundation, tmp_path)
    env[5].data["streams"].append(
        {
            "index": 2,
            "type": "subtitle",
            "codec": "hdmv_pgs_subtitle" if variant == "bitmap" else "subrip",
            "language": "chi",
            "title": {"hant": "繁体", "chi": "Chinese", "conflict": "简体 繁体"}.get(
                variant, "简体"
            ),
            "forced": int(variant == "forced"),
        }
    )
    if variant == "incomplete":
        env[5].text = SRT.replace(b"00:01:30,000", b"00:00:01,000")
    task, package = await run(env)
    if variant == "hans":
        assert task.status == "completed" and package.subtitle_status == "embedded_zh_hans"
        assert env[5].calls["extract"] == 1 and not env[6].calls
    elif variant == "conflict":
        assert package.subtitle_status == "needs_review" and env[3].calls["move"] == 0
    else:
        assert task.status == "completed" and package.subtitle_status == "downloaded_verified"


@pytest.mark.parametrize("variant", ["english", "unrelated", "html", "empty", "timeline"])
async def test_existing_external_matrix(foundation, tmp_path, variant):
    env = await setup(foundation, tmp_path)
    dav = env[3]
    name = "Other" if variant == "unrelated" else "Example.2020.1080p.en"
    content = {
        "html": b"<html>error</html>",
        "empty": b"",
        "timeline": SRT.replace(b"00:01:30,000", b"03:01:30,000"),
    }.get(variant, SRT)
    dav.files["/incoming/Example (2020)/" + name + ".srt"] = content
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert package.subtitle_status == (
        "external_verified" if variant == "english" else "downloaded_verified"
    )
    assert bool(env[6].calls) == (variant != "english")


async def test_valid_external_skips_embedded_extraction(foundation, tmp_path, monkeypatch):
    env = await setup(foundation, tmp_path)
    dav, probe, shooter = env[3], env[5], env[6]
    dav.files["/incoming/Example (2020)/Example.2020.1080p.en.srt"] = SRT
    probe.data["streams"].append(
        {
            "index": 2,
            "type": "subtitle",
            "codec": "subrip",
            "language": "chi",
            "title": "简体",
        }
    )

    async def forbidden_extract(index):
        pytest.fail("valid external must avoid unnecessary embedded extraction")

    monkeypatch.setattr(probe, "extract", forbidden_extract)
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    assert package.subtitle_status == "external_verified"
    assert probe.calls == {"probe": 1} and not shooter.calls
    assert dav.calls["media_reads"] == 0 and dav.calls["move"] == 1


async def test_no_subtitle_blocks_archive_retry_reuses_base(foundation, tmp_path):
    env = await setup(foundation, tmp_path)
    db, _, queue, dav, tmdb, probe, shooter, worker, _ = env
    shooter.empty = True
    task, package = await run(env)
    assert task.stage == Stage.SUBTITLE and task.error_code == "subtitle_no_match"
    assert dav.calls["move"] == 0
    counts = tmdb.calls.copy()
    puts = {k: v for k, v in dav.calls.items() if k.startswith("put:")}
    # Manual completion then retry of the same durable task.
    dav.files[package.remote_path + "/Example.2020.1080p.en.srt"] = SRT
    queue.control(task.id, "retry")
    await worker.execute(queue.claim("restart"))
    with db.sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
    assert tmdb.calls == counts and probe.calls["probe"] == 1
    assert all(dav.calls[k] == v for k, v in puts.items())


@pytest.mark.parametrize("mode", ["timeout_after", "timeout_before", "both", "partial", "rejected"])
async def test_move_fault_matrix(foundation, tmp_path, mode):
    env = await setup(foundation, tmp_path, original="zh")
    db, _, queue, dav, _, _, _, worker, _ = env
    dav.move_mode = mode
    task, package = await run(env)
    if mode == "timeout_after":
        assert task.status == "completed" and package.archive_status == "archived"
    else:
        assert task.status == "blocked" and package.archive_status in {
            "move_unknown",
            "partial_failure",
            "retry_safe",
        }
        assert "/incoming/Example (2020)" in dav.directories
        queue.control(task.id, "retry")
        dav.move_mode = "ok"
        await worker.execute(queue.claim("retry"))
        assert dav.calls["move"] == (2 if mode == "rejected" else 1)


async def test_move_crash_reconciles_on_restart_without_second_move(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="cn")
    db, _, queue, dav, tmdb, _, _, worker, _ = env
    dav.move_mode = "crash"
    task, package = await run(env)
    assert task.status == "queued" and task.stage == Stage.ARCHIVE
    with db.sessions.begin() as session:
        intent = session.scalar(select(ArchiveIntent))
        assert intent.status == "sending" and intent.manifest_sha256
    counts = tmdb.calls.copy()
    queue.recover()
    await worker.execute(queue.claim("new-process"))
    with db.sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
        assert session.get(Package, package.id).archive_status == "archived"
    assert dav.calls["move"] == 1 and tmdb.calls == counts


async def test_target_conflict_and_unverified_capability_block(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="zh", move_verified=False)
    task, package = await run(env)
    assert task.error_code == "move_capability_unverified" and env[3].calls["move"] == 0


async def test_conflict_preserves_both(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="zh")
    env[3].directories.add("/library/Example (2020)")
    env[3].files["/library/Example (2020)/sentinel"] = b"existing user content"
    task, _ = await run(env)
    assert task.error_code == "archive_target_conflict" and env[3].calls["move"] == 0
    assert env[3].files["/library/Example (2020)/sentinel"] == b"existing user content"


async def test_changed_attachment_blocks_move(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="zh")
    worker = env[7]
    original = worker.handlers[Stage.MANIFEST]

    async def change(claim):
        result = await original(claim)
        env[3].files["/incoming/Example (2020)/new-attachment.txt"] = b"changed"
        return result

    worker.handlers[Stage.MANIFEST] = change
    task, _ = await run(env)
    assert task.error_code == "source_changed_before_archive" and env[3].calls["move"] == 0


async def test_bad_subtitle_upload_never_moves(foundation, tmp_path):
    env = await setup(foundation, tmp_path)
    original = env[3].put

    async def corrupt(path, body):
        if path.endswith(".srt"):
            body = b"corrupt"
        await original(path, body)

    env[3].put = corrupt
    task, _ = await run(env)
    assert task.error_code == "upload_verification_failed" and env[3].calls["move"] == 0


async def test_final_manifest_failure_blocks_move(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="zh")
    original = env[3].put

    async def corrupt(path, body):
        if path.endswith(".json"):
            body = b"bad manifest"
        await original(path, body)

    env[3].put = corrupt
    task, _ = await run(env)
    assert task.stage == Stage.MANIFEST and task.error_code == "upload_verification_failed"
    assert env[3].calls["move"] == 0


async def test_database_failure_after_remote_move_recovers(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="zh")
    original = env[7].completion.complete_archive

    def fail_commit(claim, intent):
        raise RuntimeError("injected db commit failure")

    env[7].completion.complete_archive = fail_commit
    task, package = await run(env)
    assert task.error_code == "internal_worker_error"
    assert "/incoming/Example (2020)" not in env[3].directories
    env[7].completion.complete_archive = original
    env[2].control(task.id, "retry")
    await env[7].execute(env[2].claim("restart"))
    with env[0].sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
    assert env[3].calls["move"] == 1


async def test_scan_does_not_destroy_pending_move_recovery(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="zh")
    env[3].move_mode = "crash"
    task, package = await run(env)
    config, revision = env[1].load()
    await env[7].movies.scanner.scan(env[3], config, revision)
    with env[0].sessions.begin() as session:
        assert session.get(Package, package.id).context_version == package.context_version
    await env[7].execute(env[2].claim("restart"))
    with env[0].sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"


async def test_wrong_target_identity_cannot_finish_archive(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="zh")
    env[3].move_mode = "crash"
    task, package = await run(env)
    manifest = next(p for p in env[3].files if "/output/" in p or p.endswith(".json"))
    data = json.loads(env[3].files[manifest])
    data["package_id"] = "wrong-package"
    env[3].files[manifest] = json.dumps(data).encode()
    await env[7].execute(env[2].claim("restart"))
    with env[0].sessions.begin() as session:
        assert session.get(Task, task.id).error_code == "move_unknown"
    assert env[3].calls["move"] == 1


async def test_base_gate_rechecks_bytes_before_probe(foundation, tmp_path):
    env = await setup(foundation, tmp_path)
    original = env[7].handlers[Stage.BASE]

    async def change(claim):
        result = await original(claim)
        env[3].files["/incoming/Example (2020)/poster.jpg"] = b"bad"
        return result

    env[7].handlers[Stage.BASE] = change
    task, _ = await run(env)
    assert task.error_code == "base_assets_not_remote_verified"
    assert not env[5].calls and not env[6].calls and env[3].calls["media_reads"] == 0


async def test_archive_completion_checkpoint_commit_failure_is_idempotent(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="zh")
    original = env[2].finish_step

    def fail_once(claim, stage, checkpoint):
        if stage == Stage.ARCHIVE:
            raise RuntimeError("injected checkpoint commit failure")
        return original(claim, stage, checkpoint)

    env[2].finish_step = fail_once
    task, package = await run(env)
    assert task.status == "blocked" and package.archive_status == "archived"
    env[2].finish_step = original
    env[2].control(task.id, "retry")
    await env[7].execute(env[2].claim("restart"))
    from reeldock.models import Media

    with env[0].sessions.begin() as session:
        assert session.get(Task, task.id).status == "completed"
        media = session.scalar(select(Media).where(Media.package_id == package.id))
        assert media.remote_path == "/library/Example (2020)/Example.2020.1080p.mkv"
    assert env[3].calls["move"] == 1


async def test_unmanaged_control_directory_attachment_is_in_manifest(foundation, tmp_path):
    env = await setup(foundation, tmp_path, original="zh")
    env[3].directories.add("/incoming/Example (2020)/.reeldock")
    env[3].files["/incoming/Example (2020)/.reeldock/user.txt"] = b"user attachment"
    task, package = await run(env)
    assert task.status == "completed", task.error_code
    manifest = next(p for p in env[3].files if p.endswith(".json"))
    data = json.loads(env[3].files[manifest])
    assert any(row["path"] == ".reeldock/user.txt" for row in data["inventory"])


@pytest.mark.parametrize("changed", ["small", "video", "attachment"])
async def test_move_metadata_changes_require_full_hash_or_strict_versions(
    foundation, tmp_path, changed
):
    env = await setup(foundation, tmp_path, original="zh")
    dav = env[3]
    dav.files["/incoming/Example (2020)/notes.txt"] = b"ordinary attachment"
    original = dav.entry

    def entry(path):
        value = original(path)
        match = {"small": ".nfo", "video": ".mkv", "attachment": ".txt"}[changed]
        if path.startswith("/library/") and path.endswith(match):
            value.etag, value.modified = "new-etag-after-move", "new-mtime"
        return value

    dav.entry = entry
    task, package = await run(env)
    assert package.archive_status == ("archived" if changed == "small" else "move_unknown")
    assert task.status == ("completed" if changed == "small" else "blocked")
    assert dav.calls["move"] == 1


async def test_unknown_audio_preserves_actual_probe_evidence(foundation, tmp_path):
    env = await setup(foundation, tmp_path, audio="und")
    task, package = await run(env)
    from reeldock.models import Media

    with env[0].sessions.begin() as session:
        media = session.scalar(select(Media).where(Media.package_id == package.id))
        assert media.probe_status == "probed"
        assert media.probe_evidence["probe"]["streams"][1]["language"] == "und"
        assert "audio" not in media.probe_evidence
    assert task.error_code == "default_audio_unknown" and package.subtitle_status == "needs_review"


async def test_missing_duration_can_still_use_confirmed_default_chinese(foundation, tmp_path):
    env = await setup(foundation, tmp_path, audio="chi")
    env[5].data["duration"] = None
    task, package = await run(env)
    assert task.status == "completed" and package.subtitle_status == "default_audio_chinese"
    assert not env[6].calls


async def test_incomplete_idx_sub_pair_is_recorded_not_trusted(foundation, tmp_path):
    env = await setup(foundation, tmp_path)
    env[6].empty = True
    env[3].files["/incoming/Example (2020)/Example.2020.1080p.idx"] = b"index without pair"
    task, package = await run(env)
    with env[0].sessions.begin() as session:
        asset = session.scalar(select(Asset).where(Asset.kind == "subtitle"))
        assert asset.status == "failed" and asset.error_code == "subtitle_pair_incomplete"
    assert task.status == "blocked" and env[3].calls["move"] == 0
