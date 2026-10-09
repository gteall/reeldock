"""Durable queue. Every mutation, lease and corresponding event is one transaction."""

import hashlib
import json
import re
import time
from dataclasses import dataclass

from sqlalchemy import select

from reeldock.domain import BASE_KINDS, STAGES, ProviderError, Stage
from reeldock.models import Asset, Configuration, Event, Media, Package, Task, TaskStep

ACTIVE = {"queued", "running", "retry_wait", "paused", "blocked"}
SUBTITLE_PASSED = {
    "all_media_verified",
    "skipped_tmdb_chinese",
    "default_audio_chinese",
    "embedded_zh_hans",
    "external_verified",
    "downloaded_verified",
}


@dataclass(frozen=True)
class Claim:
    task_id: str
    package_id: str
    kind: str
    stage: str
    owner: str
    generation: int
    context_version: int
    config_revision: int


def emit(session, code: str, task_id: str | None = None, **details):
    session.add(Event(task_id=task_id, code=code, details=details))


def base_verified(session, package: Package) -> bool:
    """The frozen required set is nonempty and includes NFO, poster and fanart.

    A subtitle or final manifest cannot accidentally enter this prerequisite set.
    The P2 uploader will freeze the set before processing any of its members.
    """
    required = set(package.base_required)
    if not required or len(required) != len(package.base_required) or not package.tmdb_id:
        return False
    assets = list(session.scalars(select(Asset).where(Asset.package_id == package.id)))
    selected = [asset for asset in assets if asset.relative_path in required]
    if {asset.relative_path for asset in selected} != required:
        return False
    if {
        asset.relative_path for asset in assets if asset.required and asset.kind in BASE_KINDS
    } != required:
        return False
    if not {"nfo", "poster", "fanart"}.issubset({asset.kind for asset in selected}):
        return False
    if package.kind == "tv":
        media = list(session.scalars(select(Media).where(Media.package_id == package.id)))
        if not media or any(
            m.metadata_version != package.context_version
            or not m.details
            or m.original_language != package.original_language
            for m in media
        ):
            return False
        if {a.media_id for a in selected if a.kind == "nfo" and a.media_id} != {
            m.id for m in media
        } or not any(a.relative_path == "tvshow.nfo" and a.kind == "nfo" for a in selected):
            return False
    return all(
        asset.kind in BASE_KINDS
        and asset.required
        and asset.status == "remote_verified"
        and asset.sha256
        and re.fullmatch(r"[0-9a-f]{64}", asset.sha256)
        and asset.remote_verified_at is not None
        and asset.verified_version == package.context_version
        for asset in selected
    )


def gate(session, package: Package, stage: str):
    if stage in {Stage.SUBTITLE, Stage.MANIFEST, Stage.ARCHIVE} and not base_verified(
        session, package
    ):
        raise ProviderError("base_assets_not_remote_verified")
    if stage in {Stage.MANIFEST, Stage.ARCHIVE}:
        if package.kind == "tv":
            media = list(session.scalars(select(Media).where(Media.package_id == package.id)))
            for item in media:
                if (
                    item.subtitle_status not in SUBTITLE_PASSED - {"all_media_verified"}
                    or item.subtitle_version != package.context_version
                    or item.original_language != package.original_language
                ):
                    raise ProviderError("subtitle_policy_not_satisfied")
                if item.subtitle_status in {"external_verified", "downloaded_verified"}:
                    subs = list(
                        session.scalars(
                            select(Asset).where(
                                Asset.media_id == item.id,
                                Asset.kind == "subtitle",
                                Asset.required.is_(True),
                            )
                        )
                    )
                    if not subs or any(
                        a.status != "remote_verified"
                        or a.verified_version != package.context_version
                        or not a.sha256
                        or a.remote_verified_at is None
                        for a in subs
                    ):
                        raise ProviderError("subtitle_policy_not_satisfied")
        if (
            package.subtitle_status not in SUBTITLE_PASSED
            or package.subtitle_version != package.context_version
        ):
            raise ProviderError("subtitle_policy_not_satisfied")
        if package.subtitle_status in {"external_verified", "downloaded_verified"}:
            subtitles = list(
                session.scalars(
                    select(Asset).where(
                        Asset.package_id == package.id,
                        Asset.kind == "subtitle",
                        Asset.required.is_(True),
                    )
                )
            )
            if not subtitles or any(
                a.status != "remote_verified"
                or a.verified_version != package.context_version
                or not a.sha256
                or a.remote_verified_at is None
                for a in subtitles
            ):
                raise ProviderError("subtitle_policy_not_satisfied")
    if stage == Stage.ARCHIVE:
        manifests = list(
            session.scalars(
                select(Asset).where(
                    Asset.package_id == package.id,
                    Asset.kind == "manifest",
                    Asset.required.is_(True),
                )
            )
        )
        if (
            len(manifests) != 1
            or manifests[0].status != "remote_verified"
            or manifests[0].verified_version != package.context_version
            or not manifests[0].sha256
            or not re.fullmatch(r"[0-9a-f]{64}", manifests[0].sha256)
            or manifests[0].remote_verified_at is None
            or package.manifest_version != package.context_version
        ):
            raise ProviderError("final_manifest_not_remote_verified")


def invalidate_package(package: Package, reason="context_changed"):
    package.context_version += 1
    package.base_status = "pending"
    package.subtitle_status, package.subtitle_reason = "pending", reason
    package.subtitle_version = package.manifest_version = None
    package.subtitle_evidence = {}
    package.archive_status = "not_started"


def revise_package(session, package: Package, **changes):
    """P2/P3 must use this when source, identity, language or policy changes.

    Conservative invalidation: stale assets remain recorded but no longer satisfy gates.
    """
    allowed = {"source_snapshot", "tmdb_id", "original_language", "policy_version"}
    if not changes.keys() <= allowed:
        raise ValueError("unsupported_context_change")
    if any(getattr(package, key) != value for key, value in changes.items()):
        for key, value in changes.items():
            setattr(package, key, value)
        invalidate_package(package)


class Queue:
    def __init__(self, db, *, lease_seconds: float = 30):
        self.db, self.lease_seconds = db, lease_seconds

    def submit(
        self, kind: str, path: str, key: str, config_revision: int, *, checkpoint=None
    ) -> str:
        values = [kind, path, config_revision]
        if checkpoint is not None:
            values.append(checkpoint)
        payload = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
        with self.db.sessions.begin() as session:
            old = session.scalar(select(Task).where(Task.idempotency_key == key))
            if old:
                if old.payload_hash != payload:
                    raise ValueError("idempotency_key_conflict")
                return old.id
            package = session.scalar(select(Package).where(Package.remote_path == path))
            if package is None:
                package = Package(
                    remote_path=path,
                    kind="connection" if kind in {"connection_check", "scan"} else "movie",
                )
                session.add(package)
                session.flush()
            task = Task(
                package_id=package.id,
                kind=kind,
                idempotency_key=key,
                payload_hash=payload,
                context_version=package.context_version,
                config_revision=config_revision,
                stage=kind if kind in {"connection_check", "scan", "asset_retry"} else Stage.MATCH,
            )
            session.add(task)
            session.flush()
            stages = (
                [kind]
                if kind in {"connection_check", "scan", "asset_retry"}
                else (STAGES[:2] if kind == "movie_base" else STAGES)
            )
            for stage in stages:
                session.add(TaskStep(task_id=task.id, stage=stage, checkpoint=checkpoint or {}))
            emit(session, "task_submitted", task.id, kind=kind)
            return task.id

    def _release(self, task, package):
        task.lease_owner = task.lease_generation = None
        package.lease_owner = package.lease_until = None
        task.updated_at = time.time()

    def _recover(self, session, now):
        for task in session.scalars(select(Task).where(Task.status == "running")):
            package = session.get(Package, task.package_id)
            if package.lease_until is None or package.lease_until <= now:
                task.status = "paused" if task.pause_requested else "queued"
                # An interrupted attempt isn't a failed remote request; preserve step checkpoints.
                self._release(task, package)
                emit(session, "task_recovered", task.id)

    def recover(self):
        with self.db.sessions.begin() as session:
            self._recover(session, time.time())

    def claim(self, owner: str) -> Claim | None:
        now = time.time()
        with self.db.sessions.begin() as session:
            self._recover(session, now)
            tasks = session.scalars(
                select(Task)
                .where(
                    Task.status.in_(["queued", "retry_wait"]),
                    Task.retry_at <= now,
                )
                .order_by(Task.created_at, Task.id)
            )
            for task in tasks:
                package = session.get(Package, task.package_id)
                if package.lease_owner and (package.lease_until or 0) > now:
                    continue
                if task.pause_requested:
                    task.status = "paused"
                    continue
                if task.context_version != package.context_version:
                    task.status, task.error_code = "blocked", "context_changed_submit_new_task"
                    emit(session, "task_blocked", task.id, error_code=task.error_code)
                    continue
                task.status, task.lease_owner = "running", owner
                task.attempts += 1
                task.error_code, task.updated_at = None, now
                package.lease_owner, package.lease_until = owner, now + self.lease_seconds
                package.lease_generation += 1
                task.lease_generation = package.lease_generation
                emit(session, "task_claimed", task.id, stage=task.stage)
                return Claim(
                    task.id,
                    package.id,
                    task.kind,
                    task.stage,
                    owner,
                    package.lease_generation,
                    task.context_version,
                    task.config_revision,
                )
        return None

    def _owned(self, session, claim: Claim) -> tuple[Task, Package]:
        task = session.get(Task, claim.task_id)
        package = session.get(Package, claim.package_id)
        if (
            task is None
            or package is None
            or task.status != "running"
            or task.lease_owner != claim.owner
            or task.lease_generation != claim.generation
            or package.lease_owner != claim.owner
            or package.lease_generation != claim.generation
            or (package.lease_until or 0) <= time.time()
        ):
            raise ProviderError("lease_lost")
        return task, package

    @staticmethod
    def _current(session, task, package):
        config = session.get(Configuration, 1)
        if task.context_version != package.context_version:
            raise ProviderError("context_changed_submit_new_task")
        if not config or config.revision != task.config_revision:
            raise ProviderError("configuration_changed_submit_new_task")

    def heartbeat(self, claim: Claim):
        with self.db.sessions.begin() as session:
            _, package = self._owned(session, claim)
            package.lease_until = time.time() + self.lease_seconds

    def begin_step(self, claim: Claim) -> str | None:
        with self.db.sessions.begin() as session:
            task, package = self._owned(session, claim)
            self._current(session, task, package)
            if task.pause_requested:
                task.status = "paused"
                self._release(task, package)
                emit(session, "task_paused", task.id)
                return None
            gate(session, package, task.stage)
            if task.kind in {"package_pipeline", "movie_base"}:
                previous = STAGES[: STAGES.index(Stage(task.stage))]
                completed = set(
                    session.scalars(
                        select(TaskStep.stage).where(
                            TaskStep.task_id == task.id, TaskStep.status == "completed"
                        )
                    )
                )
                if not set(previous) <= completed:
                    raise ProviderError("prior_step_not_completed")
            step = session.scalar(
                select(TaskStep).where(
                    TaskStep.task_id == task.id,
                    TaskStep.stage == task.stage,
                )
            )
            step.status, step.error_code = "running", None
            step.attempts += 1
            step.updated_at = time.time()
            emit(session, "step_started", task.id, stage=task.stage)
            return task.stage

    def finish_step(self, claim: Claim, stage: str, checkpoint: dict | None = None):
        with self.db.sessions.begin() as session:
            task, package = self._owned(session, claim)
            self._current(session, task, package)
            if task.stage != stage:
                raise ProviderError("stage_changed")
            # Recheck after remote work, and verify the postconditions for completed stages.
            gate(session, package, stage)
            if stage == Stage.BASE and not base_verified(session, package):
                raise ProviderError("base_assets_not_remote_verified")
            if stage == Stage.SUBTITLE:
                gate(session, package, Stage.MANIFEST)
            if stage == Stage.MANIFEST:
                gate(session, package, Stage.ARCHIVE)
            step = session.scalar(
                select(TaskStep).where(
                    TaskStep.task_id == task.id,
                    TaskStep.stage == stage,
                )
            )
            step.status, step.checkpoint, step.updated_at = (
                "completed",
                checkpoint or {},
                time.time(),
            )
            emit(session, "step_completed", task.id, stage=stage)
            if stage == Stage.BASE:
                package.base_status = "remote_verified"
            if stage in {"connection_check", "scan", "asset_retry", Stage.ARCHIVE} or (
                task.kind == "movie_base" and stage == Stage.BASE
            ):
                task.status = "completed"
                self._release(task, package)
                emit(session, "task_completed", task.id)
            else:
                task.stage = STAGES[STAGES.index(Stage(stage)) + 1]
                task.updated_at = time.time()

    def fail(self, claim: Claim, error: ProviderError):
        with self.db.sessions.begin() as session:
            task, package = self._owned(session, claim)
            step = session.scalar(
                select(TaskStep).where(
                    TaskStep.task_id == task.id,
                    TaskStep.stage == task.stage,
                )
            )
            task.error_code = step.error_code = error.code
            step.status, step.updated_at = "failed", time.time()
            if task.pause_requested:
                task.status = "paused"
            elif error.retryable and step.attempts < task.max_attempts:
                task.status, task.retry_at = "retry_wait", time.time() + 2**step.attempts
            else:
                task.status = "failed" if error.retryable else "blocked"
            self._release(task, package)
            emit(session, "task_" + task.status, task.id, error_code=error.code, stage=task.stage)

    def interrupt(self, claim: Claim):
        with self.db.sessions.begin() as session:
            task, package = self._owned(session, claim)
            task.status = "paused" if task.pause_requested else "queued"
            self._release(task, package)
            emit(session, "task_interrupted", task.id)

    def control(self, task_id: str, action: str):
        with self.db.sessions.begin() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise LookupError("task_not_found")
            if action == "pause" and task.status in ACTIVE:
                task.pause_requested = True
                if task.status != "running":
                    task.status = "paused"
            elif action in {"resume", "retry"} and task.status in {
                "paused",
                "blocked",
                "failed",
                "retry_wait",
            }:
                task.pause_requested, task.status = False, "queued"
                task.retry_at, task.error_code = 0, None
                step = session.scalar(
                    select(TaskStep).where(
                        TaskStep.task_id == task.id,
                        TaskStep.stage == task.stage,
                    )
                )
                step.attempts = 0
            else:
                raise ValueError("task_action_not_allowed")
            task.updated_at = time.time()
            emit(session, "task_" + action + "_requested", task.id)
