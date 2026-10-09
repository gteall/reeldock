import asyncio
import copy
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from p2_support import FakeTMDB, MemoryDAV

from reeldock.domain import ConfigUpdate, ProviderError, SubtitleCandidate
from reeldock.runtime import Runtime
from reeldock.worker import Worker

SRT = b"1\n00:00:00,100 --> 00:01:30,000\nReelDock generated dialogue\n\n"


class DAV(MemoryDAV):
    def __init__(self):
        super().__init__()
        self.order = []
        self.move_mode = "ok"

    async def read_small(self, path, max_bytes):
        assert path not in self.media, "video GET without Range is forbidden"
        self.order.append("readback:" + PurePosixPath(path).name)
        self.calls["get:" + PurePosixPath(path).name] += 1
        if path not in self.files:
            raise ProviderError("storage_not_found")
        if len(self.files[path]) > max_bytes:
            raise ProviderError("storage_byte_budget")
        return self.files[path]

    async def put(self, path, content):
        self.calls["put:" + PurePosixPath(path).name] += 1
        self.order.append("put:" + PurePosixPath(path).name)
        if path in self.files:
            raise ProviderError("storage_conflict")
        self.files[path] = b"corrupt" if PurePosixPath(path).name in self.corrupt else content
        if PurePosixPath(path).name in self.timeout_after_put:
            raise ProviderError("storage_timeout", retryable=True)

    async def read_range(self, path, start, length, size):
        self.calls["media_reads"] += 1
        self.order.append("range")
        return bytes(i % 251 for i in range(start, start + length))

    async def capabilities(self):
        return {"remote_move_verified": True}

    def perform_move(self, source, target, *, copy_only=False):
        for mapping in [self.media, self.files]:
            for path in list(mapping):
                if path.startswith(source + "/"):
                    mapping[target + path[len(source) :]] = mapping[path]
                    if not copy_only:
                        del mapping[path]
        for path in list(self.directories):
            if path == source or path.startswith(source + "/"):
                self.directories.add(target + path[len(source) :])
                if not copy_only:
                    self.directories.remove(path)

    async def move(self, source, target):
        self.calls["move"] += 1
        self.order.append("move")
        if self.move_mode == "rejected":
            raise ProviderError("storage_permission_denied")
        if self.move_mode == "timeout_before":
            raise ProviderError("storage_timeout", retryable=True)
        self.perform_move(source, target, copy_only=self.move_mode in {"both", "partial"})
        if self.move_mode in {"crash", "crash_committed"}:
            raise asyncio.CancelledError()
        if self.move_mode == "timeout_after":
            raise ProviderError("storage_timeout", retryable=True)
        if self.move_mode == "partial":
            raise ProviderError("storage_move_partial_failure")


class Probe:
    def __init__(self, dav, language="eng", streams=None):
        self.dav = dav
        self.calls = Counter()
        self.data = {
            "duration": 100.0,
            "streams": streams
            or [
                {"index": 0, "type": "video", "codec": "h264"},
                {"index": 1, "type": "audio", "language": language, "default": 1, "title": ""},
            ],
        }
        self.text = SRT.replace(b"ReelDock generated dialogue", "影坞生成的简体对白".encode())

    @asynccontextmanager
    async def session(self, storage, media, config, check):
        check()
        self.check = check
        yield self

    async def probe(self):
        self.check()
        self.calls["probe"] += 1
        self.dav.order.append("probe")
        return copy.deepcopy(self.data)

    async def extract(self, index):
        self.calls["extract"] += 1
        self.dav.order.append("extract")
        return self.text


class Shooter:
    def __init__(self, dav):
        self.dav, self.calls = dav, Counter()
        self.content, self.empty = SRT, False

    def __call__(self, config):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def search(self, fingerprint, episode_context=None):
        self.calls["search"] += 1
        self.dav.order.append("shooter")
        return (
            []
            if self.empty
            else [
                SubtitleCandidate(
                    source="shooter",
                    external_id="1",
                    download_url="https://shooter.cn/sub.srt",
                    format="srt",
                )
            ]
        )

    async def download(self, candidate):
        self.calls["download"] += 1
        self.dav.order.append("download")
        return self.content


async def setup(foundation, tmp_path, original="en", audio="eng", **changes):
    db, store, queue = foundation
    config, revision = store.load()
    store.save(
        ConfigUpdate(
            **{**config.model_dump(), "move_verified": True, **changes}, expected_revision=revision
        )
    )
    config, revision = store.load()
    dav, tmdb = DAV(), FakeTMDB()
    tmdb.movies[0].original_language = original
    probe, shooter = Probe(dav, audio), Shooter(dav)
    worker = Worker(
        queue,
        store,
        Runtime(data_dir=tmp_path),
        storage_factory=dav,
        metadata_factory=tmdb,
        probe=probe,
        subtitle_factory=shooter,
    )
    await worker.movies.scanner.scan(
        dav, config, revision, now=time.time() - config.stable_seconds - 1
    )
    await worker.movies.scanner.scan(dav, config, revision)
    return db, store, queue, dav, tmdb, probe, shooter, worker, revision


async def run(env, key="full"):
    db, store, queue, dav, tmdb, probe, shooter, worker, revision = env
    path = "/incoming/Example (2020)"
    task_id = queue.submit("package_pipeline", path, key, revision)
    claim = queue.claim("p3-owner")
    await worker.execute(claim)
    from reeldock.models import Package, Task

    with db.sessions.begin() as session:
        return session.get(Task, task_id), session.get(Package, claim.package_id)
