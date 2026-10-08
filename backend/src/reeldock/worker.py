import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace

from reeldock.domain import ProviderError
from reeldock.providers.webdav import WebDAVProvider
from reeldock.queue import Claim, Queue

logger = logging.getLogger(__name__)
Handler = Callable[[Claim], Awaitable[dict | None]]


class Worker:
    def __init__(
        self,
        queue: Queue,
        settings,
        runtime,
        *,
        handlers: dict[str, Handler] | None = None,
        storage_factory=WebDAVProvider,
    ):
        self.queue, self.settings, self.runtime = queue, settings, runtime
        self.storage_factory = storage_factory
        self.owner = uuid.uuid4().hex
        # There are no production media, subtitle, or archive handlers in P1.
        self.handlers = {"connection_check": self.connection_check, **(handlers or {})}
        self.slots: list[asyncio.Task] = []

    async def connection_check(self, claim: Claim) -> dict:
        config, revision = self.settings.load()
        if not config or revision != claim.config_revision:
            raise ProviderError("configuration_changed_submit_new_task")
        async with self.storage_factory(config) as storage:
            input_entries = await storage.list(config.input_path)
            output_entries = await storage.list(config.output_path)
        # No filenames, server addresses or credentials in events/checkpoints.
        return {
            "input_entries": len(input_entries),
            "output_entries": len(output_entries),
            "mode": "read_only",
            "writes_tested": False,
            "move_tested": False,
        }

    async def heartbeat(self, claim: Claim, job: asyncio.Task):
        try:
            while True:
                await asyncio.sleep(self.queue.lease_seconds / 3)
                self.queue.heartbeat(claim)
        except Exception:
            # A renewal failure means ownership is uncertain. Cancel only this job,
            # never the persistent polling slot that must accept subsequent work.
            job.cancel()

    async def run_steps(self, claim: Claim):
        while True:
            stage = self.queue.begin_step(claim)
            if stage is None:
                return
            if stage == "archive":
                # Defense in depth: no injected/accidentally registered handler can MOVE in P1.
                raise ProviderError("archive_disabled_p1")
            handler = self.handlers.get(stage)
            if handler is None:
                raise ProviderError("stage_not_implemented_p1")
            checkpoint = await handler(replace(claim, stage=stage))
            self.queue.finish_step(claim, stage, checkpoint)
            if stage == "connection_check":
                return

    async def execute(self, claim: Claim):
        job = asyncio.create_task(self.run_steps(claim))
        heartbeat = asyncio.create_task(self.heartbeat(claim, job))
        try:
            await job
        except asyncio.CancelledError:
            try:
                self.queue.interrupt(claim)
            except ProviderError:
                pass  # A stale lease owner must not mutate the new owner's state.
            if asyncio.current_task().cancelling():
                raise  # Application shutdown cancels the polling slot too.
        except Exception as error:
            safe = (
                error
                if isinstance(error, ProviderError)
                else ProviderError("internal_worker_error")
            )
            try:
                self.queue.fail(claim, safe)
            except ProviderError:
                pass
            logger.warning("", extra={"safe_code": safe.code})
        finally:
            heartbeat.cancel()
            job.cancel()
            await asyncio.gather(heartbeat, job, return_exceptions=True)

    async def loop(self):
        while True:
            try:
                claim = self.queue.claim(self.owner)
                if claim:
                    await self.execute(claim)
                else:
                    await asyncio.sleep(self.runtime.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("", extra={"safe_code": "worker_poll_failed"})
                await asyncio.sleep(self.runtime.poll_seconds)

    async def start(self):
        self.queue.recover()
        self.slots = [
            asyncio.create_task(self.loop()) for _ in range(self.runtime.worker_concurrency)
        ]

    async def stop(self):
        for slot in self.slots:
            slot.cancel()
        await asyncio.gather(*self.slots, return_exceptions=True)
        self.slots.clear()
