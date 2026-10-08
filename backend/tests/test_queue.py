import asyncio
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import select

from reeldock.database import Database
from reeldock.domain import ProviderError, Stage
from reeldock.models import Asset, Package, Task, TaskStep
from reeldock.queue import Queue, base_verified, gate, revise_package
from reeldock.runtime import Runtime
from reeldock.worker import Worker


def package(foundation, *, key="job", path="/incoming/movie"):
    _, _, queue = foundation
    task_id = queue.submit("package_pipeline", path, key, 1)
    with queue.db.sessions.begin() as session:
        package_id = session.get(Task, task_id).package_id
    return task_id, package_id


def assets_ready(db, package_id):
    with db.sessions.begin() as session:
        item = session.get(Package, package_id)
        item.tmdb_id, item.original_language = "123", "en"
        item.base_required = ["movie.nfo", "poster.jpg", "fanart.jpg", ".actors/Actor.jpg"]
        for path, kind in zip(
            item.base_required, ["nfo", "poster", "fanart", "actor"], strict=True
        ):
            session.add(
                Asset(
                    package_id=package_id,
                    relative_path=path,
                    kind=kind,
                    status="remote_verified",
                    sha256="a" * 64,
                    verified_version=item.context_version,
                    remote_verified_at=time.time(),
                )
            )


def test_competing_claims_and_package_exclusion(foundation):
    _, _, queue = foundation
    package(foundation, key="first")
    package(foundation, key="second")
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(queue.claim, [f"owner{i}" for i in range(8)]))
    active = [claim for claim in claims if claim]
    assert len(active) == 1
    queue.interrupt(active[0])
    assert queue.claim("next") is not None


def test_idempotency_is_atomic_under_concurrent_submit(foundation):
    _, _, queue = foundation
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(
            pool.map(
                lambda _: queue.submit("package_pipeline", "/incoming/movie", "same-key", 1),
                range(8),
            )
        )
    assert len(set(ids)) == 1


def test_unclean_process_exit_recovers_committed_checkpoint(foundation):
    db, _, queue = foundation
    task_id, _ = package(foundation)
    script = """
import os
import sys
from pathlib import Path
from reeldock.database import Database
from reeldock.queue import Queue
db = Database(Path(sys.argv[1]))
queue = Queue(db, lease_seconds=0.2)
claim = queue.claim('crashed-process')
queue.begin_step(claim)
queue.finish_step(claim, 'match_metadata', {'durable': True})
os._exit(17)
"""
    process = subprocess.run(
        [sys.executable, "-c", script, db.engine.url.database], capture_output=True, timeout=10
    )
    assert process.returncode == 17, process.stderr.decode()
    time.sleep(0.25)
    recovered = queue.claim("replacement-process")
    assert recovered.task_id == task_id and recovered.stage == Stage.BASE
    with db.sessions.begin() as session:
        step = session.scalar(
            select(TaskStep).where(TaskStep.task_id == task_id, TaskStep.stage == Stage.MATCH)
        )
        assert step.checkpoint == {"durable": True}


def test_expired_lease_fences_old_worker_and_restarts_checkpoint(foundation):
    db, _, queue = foundation
    task_id, _ = package(foundation)
    first = queue.claim("old")
    queue.begin_step(first)
    queue.finish_step(first, Stage.MATCH, {"matched": True})
    with db.sessions.begin() as session:
        session.get(Package, first.package_id).lease_until = time.time() - 1
    restarted_db = Database(Path(db.engine.url.database))
    restarted_db.migrate()
    restarted = Queue(restarted_db)
    second = restarted.claim("new")
    assert second.task_id == task_id and second.stage == Stage.BASE
    assert second.generation > first.generation
    with pytest.raises(ProviderError, match="lease_lost"):
        queue.heartbeat(first)
    with pytest.raises(ProviderError, match="lease_lost"):
        queue.finish_step(first, Stage.BASE)
    with db.sessions.begin() as session:
        step = session.scalar(
            select(TaskStep).where(TaskStep.task_id == task_id, TaskStep.stage == Stage.MATCH)
        )
        assert step.status == "completed" and step.checkpoint == {"matched": True}
    restarted_db.close()


def test_pause_retains_lease_until_step_boundary_and_survives_restart(foundation):
    db, _, queue = foundation
    task_id, _ = package(foundation)
    claim = queue.claim("owner")
    queue.begin_step(claim)
    queue.control(task_id, "pause")
    package(foundation, key="second")
    assert queue.claim("another") is None
    queue.finish_step(claim, Stage.MATCH)
    assert queue.begin_step(claim) is None
    queue.recover()
    with db.sessions.begin() as session:
        assert session.get(Task, task_id).status == "paused"
    queue.control(task_id, "resume")
    assert queue.claim("resume").stage == Stage.BASE


async def test_order_retry_reuses_completed_base_and_archive_is_disabled(foundation, tmp_path):
    db, store, queue = foundation
    task_id, package_id = package(foundation)
    calls = []

    async def match(claim):
        calls.append("match")

    async def base(claim):
        calls.append("base")
        assets_ready(db, package_id)

    async def subtitle(claim):
        with db.sessions.begin() as session:
            assert base_verified(session, session.get(Package, package_id))
        calls.append("subtitle")
        if calls.count("subtitle") == 1:
            raise ProviderError("subtitle_network_error", retryable=True)
        with db.sessions.begin() as session:
            item = session.get(Package, package_id)
            item.subtitle_status, item.subtitle_version = "external_verified", item.context_version

    async def manifest(claim):
        calls.append("manifest")
        with db.sessions.begin() as session:
            item = session.get(Package, package_id)
            item.manifest_version = item.context_version
            session.add(
                Asset(
                    package_id=package_id,
                    relative_path=".reeldock/manifest.json",
                    kind="manifest",
                    status="remote_verified",
                    sha256="b" * 64,
                    verified_version=item.context_version,
                    remote_verified_at=time.time(),
                )
            )

    async def forbidden_archive(claim):
        pytest.fail("P1 must never invoke archive")

    worker = Worker(
        queue,
        store,
        Runtime(data_dir=tmp_path),
        handlers={
            Stage.MATCH: match,
            Stage.BASE: base,
            Stage.SUBTITLE: subtitle,
            Stage.MANIFEST: manifest,
            Stage.ARCHIVE: forbidden_archive,
        },
    )
    await worker.execute(queue.claim("first"))
    with db.sessions.begin() as session:
        task = session.get(Task, task_id)
        assert task.status == "retry_wait" and task.stage == Stage.SUBTITLE
        task.retry_at = 0
    await worker.execute(queue.claim("restart"))
    assert calls == ["match", "base", "subtitle", "subtitle", "manifest"]
    with db.sessions.begin() as session:
        task = session.get(Task, task_id)
        assert task.status == "blocked" and task.error_code == "archive_disabled_p1"


@pytest.mark.parametrize(
    "defect", ["empty", "missing", "pending", "stale", "subtitle_in_base", "actor_failed"]
)
async def test_incomplete_base_blocks_subtitle_handler(foundation, tmp_path, defect):
    db, store, queue = foundation
    task_id, package_id = package(foundation)
    assets_ready(db, package_id)
    with db.sessions.begin() as session:
        item = session.get(Package, package_id)
        task = session.get(Task, task_id)
        task.stage = Stage.SUBTITLE
        for step in session.scalars(
            select(TaskStep).where(
                TaskStep.task_id == task_id, TaskStep.stage.in_([Stage.MATCH, Stage.BASE])
            )
        ):
            step.status = "completed"
        if defect == "empty":
            item.base_required = []
        elif defect == "missing":
            item.base_required = [*item.base_required, "missing.nfo"]
        elif defect == "stale":
            item.context_version += 1
            task.context_version += 1
        elif defect == "subtitle_in_base":
            asset = session.scalar(
                select(Asset).where(Asset.package_id == package_id, Asset.kind == "nfo")
            )
            asset.kind = "subtitle"
        else:
            kind = "actor" if defect == "actor_failed" else "poster"
            session.scalar(
                select(Asset).where(Asset.package_id == package_id, Asset.kind == kind)
            ).status = "failed"
    called = []

    async def forbidden(claim):
        called.append(claim.stage)

    worker = Worker(queue, store, Runtime(data_dir=tmp_path), handlers={Stage.SUBTITLE: forbidden})
    await worker.execute(queue.claim("owner"))
    assert called == []
    with db.sessions.begin() as session:
        assert session.get(Task, task_id).error_code == "base_assets_not_remote_verified"


@pytest.mark.parametrize(
    "change",
    [
        {"tmdb_id": "999"},
        {"original_language": "zh"},
        {"source_snapshot": {"size": 2}},
        {"policy_version": 2},
    ],
)
def test_context_change_invalidates_checkpoints(foundation, change):
    db, _, queue = foundation
    task_id, package_id = package(foundation)
    assets_ready(db, package_id)
    with db.sessions.begin() as session:
        item = session.get(Package, package_id)
        assert base_verified(session, item)
        revise_package(session, item, **change)
        assert not base_verified(session, item)
        assert item.subtitle_version is None
    assert queue.claim("owner") is None
    with db.sessions.begin() as session:
        assert session.get(Task, task_id).error_code == "context_changed_submit_new_task"


def test_final_manifest_gate_checks_remote_evidence(foundation):
    db, _, _ = foundation
    _, package_id = package(foundation)
    assets_ready(db, package_id)
    with db.sessions.begin() as session:
        item = session.get(Package, package_id)
        with pytest.raises(ProviderError, match="subtitle_policy_not_satisfied"):
            gate(session, item, Stage.MANIFEST)
        item.subtitle_status, item.subtitle_version = "skipped_tmdb_chinese", item.context_version
        with pytest.raises(ProviderError, match="final_manifest_not_remote_verified"):
            gate(session, item, Stage.ARCHIVE)


async def test_worker_heartbeats_and_graceful_restart(foundation, tmp_path):
    db, store, queue = foundation
    queue.lease_seconds = 0.18
    task_id, package_id = package(foundation)
    entered = asyncio.Event()

    async def slow(claim):
        entered.set()
        await asyncio.sleep(30)

    runtime = Runtime(data_dir=tmp_path, poll_seconds=0.05, worker_concurrency=1)
    worker = Worker(queue, store, runtime, handlers={Stage.MATCH: slow})
    await worker.start()
    await asyncio.wait_for(entered.wait(), timeout=2)
    await asyncio.sleep(0.3)
    with db.sessions.begin() as session:
        assert session.get(Package, package_id).lease_until > time.time()
    assert queue.claim("competitor") is None
    await worker.stop()
    assert queue.claim("restarted").task_id == task_id


async def test_verified_assets_do_not_allow_skipping_predecessor_checkpoints(foundation, tmp_path):
    db, store, queue = foundation
    task_id, package_id = package(foundation)
    assets_ready(db, package_id)
    with db.sessions.begin() as session:
        session.get(Task, task_id).stage = Stage.SUBTITLE
    calls = []

    async def forbidden(claim):
        calls.append(claim.stage)

    worker = Worker(queue, store, Runtime(data_dir=tmp_path), handlers={Stage.SUBTITLE: forbidden})
    await worker.execute(queue.claim("owner"))
    assert not calls
    with db.sessions.begin() as session:
        assert session.get(Task, task_id).error_code == "prior_step_not_completed"


def test_automatic_retry_is_bounded_and_manual_retry_resets_current_step(foundation):
    db, _, queue = foundation
    task_id = queue.submit("connection_check", ":connection", "retry-budget", 1)
    for attempt in range(3):
        claim = queue.claim("worker")
        queue.begin_step(claim)
        queue.fail(claim, ProviderError("storage_timeout", retryable=True))
        with db.sessions.begin() as session:
            task = session.get(Task, task_id)
            assert task.status == ("failed" if attempt == 2 else "retry_wait")
            task.retry_at = 0
    assert queue.claim("worker") is None
    queue.control(task_id, "retry")
    claim = queue.claim("worker")
    queue.begin_step(claim)
    queue.finish_step(claim, "connection_check", {"mode": "read_only"})
    with db.sessions.begin() as session:
        assert session.get(Task, task_id).status == "completed"


async def test_configuration_change_in_flight_rejects_stale_completion(
    foundation, tmp_path, config_body
):
    from reeldock.domain import ConfigUpdate

    db, store, queue = foundation
    task_id, _ = package(foundation)

    async def changed(claim):
        store.save(ConfigUpdate(**config_body, expected_revision=1))

    worker = Worker(queue, store, Runtime(data_dir=tmp_path), handlers={Stage.MATCH: changed})
    await worker.execute(queue.claim("owner"))
    with db.sessions.begin() as session:
        task = session.get(Task, task_id)
        assert task.status == "blocked" and task.stage == Stage.MATCH
        assert task.error_code == "configuration_changed_submit_new_task"
