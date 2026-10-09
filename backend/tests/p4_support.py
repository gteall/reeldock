import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from p2_support import FakeTMDB
from p3_support import DAV, Probe, Shooter

from reeldock.domain import Artwork, ConfigUpdate, Episode, ProviderError, Season, Series
from reeldock.models import Package, Task
from reeldock.runtime import Runtime
from reeldock.worker import Worker

ROOT = "/incoming/Example (2020)"


class TVMetadata(FakeTMDB):
    def __init__(self, original="zh"):
        super().__init__()
        self.series = Series(**{**self.movies[0].model_dump(), "original_language": original})
        self.failed_episodes = set()

    async def search(self, query, kind, year=None):
        self.calls["search:" + kind] += 1
        return [self.series] if kind == "tv" else self.movies

    async def series_details(self, identifier):
        self.calls["series"] += 1
        if identifier != self.series.external_id:
            raise ProviderError("tmdb_not_found")
        return self.series

    async def season_details(self, identifier, season):
        self.calls[f"season:{season}"] += 1
        return Season(series_id=identifier, season=season, title=f"Season {season}")

    async def episode_details(self, identifier, season, episode):
        self.calls[f"episode:{season}:{episode}"] += 1
        if episode in self.failed_episodes:
            raise ProviderError("tmdb_not_found")
        return Episode(
            source="tmdb",
            external_id=str(1000 + season * 100 + episode),
            title=f"Episode {episode}",
            series_id=identifier,
            season=season,
            episode=episode,
            original_language="de",
            overview="Generated episode",
        )

    async def images(self, external_id, kind="movie", *, season=None, episode=None):
        if episode is not None:
            kinds = ["thumb"]
        elif season is not None:
            kinds = ["season_poster"]
        else:
            kinds = ["poster", "fanart"]
        self.calls[f"images:{season}:{episode}"] += 1
        return [Artwork(source="tmdb", external_id=f"/{k}.jpg", kind=k) for k in kinds]


class TVProbe(Probe):
    def __init__(self, dav):
        super().__init__(dav)
        self.media_calls = Counter()

    @asynccontextmanager
    async def session(self, storage, media, config, check):
        self.path, self.check = media.remote_path, check
        yield self

    async def probe(self):
        self.media_calls[self.path] += 1
        return await super().probe()


class TVShooter(Shooter):
    def __init__(self, dav):
        super().__init__(dav)
        self.missing = set()
        self.queries = Counter()

    async def search(self, fingerprint, episode_context=None):
        self.queries[episode_context["filename"]] += 1
        results = await super().search(fingerprint, episode_context)
        return [] if episode_context["episode"] in self.missing else results


async def setup_tv(foundation, tmp_path, original="zh", files=None):
    db, store, queue = foundation
    config, revision = store.load()
    store.save(
        ConfigUpdate(
            **{**config.model_dump(), "stable_seconds": 1, "move_verified": True},
            expected_revision=revision,
        )
    )
    config, revision = store.load()
    dav = DAV()
    dav.directories = {"/incoming", "/library", ROOT}
    dav.media = {
        ROOT + "/" + name: 1024**3
        for name in (files or ["Season 01/Example.S01E01.mkv", "Season 01/Example.S01E02.mkv"])
    }
    for path in dav.media:
        dav.directories.update(
            str(parent) for parent in PurePosixPath(path).parents if str(parent).startswith(ROOT)
        )
    metadata, probe, shooter = TVMetadata(original), TVProbe(dav), TVShooter(dav)
    worker = Worker(
        queue,
        store,
        Runtime(data_dir=tmp_path),
        storage_factory=dav,
        metadata_factory=metadata,
        probe=probe,
        subtitle_factory=shooter,
    )
    await worker.movies.scanner.scan(dav, config, revision, now=time.time() - 2)
    await worker.movies.scanner.scan(dav, config, revision)
    return db, store, queue, dav, metadata, probe, shooter, worker, revision


async def run_tv(env, key="tv-full"):
    db, _, queue, _, _, _, _, worker, revision = env
    task_id = queue.submit("package_pipeline", ROOT, key, revision)
    claim = queue.claim("tv-worker")
    await worker.execute(claim)
    with db.sessions.begin() as session:
        return session.get(Task, task_id), session.get(Package, claim.package_id)
