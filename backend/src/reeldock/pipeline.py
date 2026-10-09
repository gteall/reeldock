"""P2 film pipeline: directory metadata -> TMDB -> verified base assets, then STOP."""

import re
import time
from pathlib import PurePosixPath

from sqlalchemy import select

from reeldock.cache import Cache, sha256
from reeldock.domain import Artwork, Movie, Person, ProviderError
from reeldock.exporter import (
    MAX_ASSET_BYTES,
    MAX_NFO_BYTES,
    actor_filename,
    export_nfo,
    imdb_ids,
    nfo_ids,
    read_nfo,
    validate_image,
)
from reeldock.matching import automatic_candidate, explicit_ids, parse_name, rank_candidates
from reeldock.models import Asset, Media, MovieRecord, Package, TaskStep
from reeldock.providers.tmdb import TMDBProvider
from reeldock.providers.webdav import WebDAVProvider
from reeldock.queue import emit, invalidate_package, revise_package
from reeldock.scanner import (
    Scanner,
    collect,
    flatten,
    media_entries,
    package_reason,
    source_snapshot,
)
from reeldock.series import asset_descriptions, match_series


def art_data(art):
    return art.model_dump(exclude={"url"}) if art else None


class MoviePipeline:
    def __init__(
        self,
        queue,
        settings,
        runtime,
        *,
        storage_factory=WebDAVProvider,
        metadata_factory=TMDBProvider,
    ):
        self.queue, self.db, self.settings = queue, queue.db, settings
        self.storage_factory, self.metadata_factory = storage_factory, metadata_factory
        self.cache = Cache(self.db, runtime.data_dir / "cache" / "assets")
        self.scanner = Scanner(self.db)

    def check(self, claim):
        with self.db.sessions.begin() as session:
            task, package = self.queue._owned(session, claim)
            self.queue._current(session, task, package)
            if task.pause_requested:
                raise ProviderError("pause_requested", retryable=True)

    def configuration(self, claim):
        self.check(claim)
        config, revision = self.settings.load()
        if not config or revision != claim.config_revision:
            raise ProviderError("configuration_changed_submit_new_task")
        return config

    def record(self, claim):
        with self.db.sessions.begin() as session:
            package = session.get(Package, claim.package_id)
            record = session.get(MovieRecord, claim.package_id)
            if not record or (
                record.scan_status != "stable"
                and not (claim.kind == "asset_retry" and record.scan_status == "archived")
            ):
                raise ProviderError("package_not_stable")
            return package, record

    async def scan(self, claim):
        config = self.configuration(claim)
        async with self.storage_factory(config) as storage:
            return await self.scanner.scan(
                storage, config, claim.config_revision, check=lambda: self.check(claim)
            )

    async def source(self, claim, storage, config):
        self.check(claim)
        package, record = self.record(claim)
        entries = (
            flatten(
                await collect(storage, package.remote_path, lambda: self.check(claim)),
                package.remote_path,
            )
            if package.kind == "tv"
            else await storage.list(package.remote_path)
        )
        with self.db.sessions.begin() as session:
            mappings = {
                m.remote_path: (m.season, m.episode, m.manual_mapping)
                for m in session.scalars(select(Media).where(Media.package_id == package.id))
            }
        snapshot = source_snapshot(entries)
        snapshot["config_revision"] = claim.config_revision
        # Protected pre-P4 MOVE intents keep their historical snapshot format.
        if "kind" in package.source_snapshot:
            snapshot["kind"] = package.kind
        reason = package_reason(
            package.remote_path,
            config.input_path,
            entries,
            media_entries(entries),
            package.kind,
            mappings,
        )
        if snapshot != package.source_snapshot or reason:
            with self.db.sessions.begin() as session:
                task, current = self.queue._owned(session, claim)
                self.queue._current(session, task, current)
                revise_package(session, current, source_snapshot=snapshot)
                row = session.get(MovieRecord, current.id)
                row.scan_status, row.stable_since = "waiting_stable", time.time()
                row.error_code = reason or "source_changed"
            raise ProviderError("source_changed_scan_again")
        self.check(claim)
        return entries

    async def match(self, claim):
        package, _ = self.record(claim)
        if package.kind == "tv":
            return await match_series(self, claim)
        config = self.configuration(claim)
        package, record = self.record(claim)
        async with (
            self.storage_factory(config) as storage,
            self.metadata_factory(config, self.cache) as metadata,
        ):
            entries = await self.source(claim, storage, config)
            parsed = parse_name(record.media_path)
            filename_ids = explicit_ids(package.remote_path) | explicit_ids(record.media_path)
            ids = set(filename_ids)
            imdb_identifiers = set(
                re.findall(
                    r"(?i)(?<![a-z0-9])tt\d{5,12}(?!\d)",
                    package.remote_path + "/" + record.media_path,
                )
            )
            nfos = [
                e
                for e in entries
                if not e.is_dir and PurePosixPath(e.path).suffix.lower() == ".nfo"
            ]
            # Kodi has two supported NFO conventions; multiple or unrelated files are ambiguous.
            allowed = {
                str(PurePosixPath(record.media_path).with_suffix(".nfo")),
                package.remote_path + "/movie.nfo",
            }
            if len(nfos) > 1 or any(e.path not in allowed for e in nfos):
                raise ProviderError("multiple_nfo_requires_review")
            for entry in nfos:
                body = await storage.read_small(entry.path, MAX_NFO_BYTES)
                self.cache.put(body)  # Immutable local backup; never overwrite the source.
                self._remember_existing(claim, PurePosixPath(entry.path).name, body)
                try:
                    nfo_identifiers = nfo_ids(body)
                    nfo_imdb = imdb_ids(body)
                    imdb_identifiers |= nfo_imdb
                    if not nfo_identifiers and not nfo_imdb:
                        raise ProviderError("existing_nfo_id_missing")
                    ids |= nfo_identifiers
                except ProviderError as error:
                    with self.db.sessions.begin() as session:
                        self.queue._owned(session, claim)
                        asset = session.scalar(
                            select(Asset).where(
                                Asset.package_id == claim.package_id,
                                Asset.relative_path == PurePosixPath(entry.path).name,
                            )
                        )
                        asset.status, asset.error_code = "failed", error.code
                        session.get(Package, claim.package_id).base_status = "failed"
                    raise
            if len(imdb_identifiers) > 1:
                raise ProviderError("tmdb_id_conflict")
            if imdb_identifiers:
                found = await metadata.find_movie(next(iter(imdb_identifiers)).lower())
                ids.add(found.external_id)
            if len(ids) > 1 or (record.manual_id and ids and record.manual_id not in ids):
                raise ProviderError("tmdb_id_conflict")
            chosen = record.manual_id or next(iter(ids), None)
            candidates = record.candidates
            if chosen is None:
                movies = await metadata.search(parsed.title, "movie", parsed.year)
                candidates = rank_candidates(parsed, movies)
                chosen = automatic_candidate(candidates)
                if not chosen:
                    with self.db.sessions.begin() as session:
                        self.queue._owned(session, claim)
                        row = session.get(MovieRecord, claim.package_id)
                        row.candidates, row.match_status = candidates, "needs_review"
                        row.error_code = "tmdb_match_needs_review"
                    raise ProviderError("tmdb_match_needs_review")
            movie = await metadata.movie_details(chosen)
            if movie.external_id != chosen:
                raise ProviderError("tmdb_id_conflict")
            people = await metadata.credits(chosen, "movie")
            cast = self.cast(people, config)
            artwork = await metadata.images(chosen, "movie")
            await self.source(claim, storage, config)
            metadata_changed = False
            with self.db.sessions.begin() as session:
                task, current = self.queue._owned(session, claim)
                self.queue._current(session, task, current)
                # Initial binding is a baseline; later identity edits must happen before
                # a new claim, using revise_package (manual-selection endpoint).
                if current.tmdb_id and (
                    current.tmdb_id != movie.external_id
                    or (
                        (current.original_language is not None or current.base_required)
                        and current.original_language != movie.original_language
                    )
                ):
                    revise_package(
                        session,
                        current,
                        tmdb_id=movie.external_id,
                        original_language=movie.original_language,
                    )
                    metadata_changed = True
                current.tmdb_id, current.original_language = (
                    movie.external_id,
                    movie.original_language,
                )
                row = session.get(MovieRecord, current.id)
                row.details, row.cast, row.artwork = (
                    movie.model_dump(),
                    cast,
                    [art_data(a) for a in artwork],
                )
                row.candidates, row.match_status, row.error_code = candidates, "matched", None
                current.policy_version = config.policy_version
            if metadata_changed:
                raise ProviderError("metadata_changed_submit_new_task")
            return {
                "tmdb_id": movie.external_id,
                "original_language": movie.original_language,
                "text_language": "zh-CN",
                "actors": len(cast),
            }

    @staticmethod
    def cast(people, config):
        names = {}
        cast = []
        for index, person in enumerate(people):
            selected = config.actor_limit == 0 or index < config.actor_limit
            filename = actor_filename(person.name) if selected else None
            if selected:
                key = filename.casefold()
                if key in names and names[key] != person.external_id:
                    raise ProviderError("actor_filename_collision")
                names[key] = person.external_id
            item = person.model_dump(exclude={"profile"})
            item["profile"] = art_data(person.profile)
            item["path"] = ".actors/" + filename if filename else None
            item["selected"] = selected
            item["status"] = (
                ("pending" if person.profile else "source_no_image") if selected else "not_selected"
            )
            cast.append(item)
        return cast

    def _remember_existing(self, claim, name, body):
        with self.db.sessions.begin() as session:
            task, package = self.queue._owned(session, claim)
            self.queue._current(session, task, package)
            asset = session.scalar(
                select(Asset).where(Asset.package_id == package.id, Asset.relative_path == name)
            )
            if asset is None:
                asset = Asset(package_id=package.id, relative_path=name, kind="nfo", required=False)
                session.add(asset)
            if asset.sha256 != sha256(body):
                asset.status = "existing_unverified"
                asset.cache_key = self.cache.put(body)
                asset.managed = False
                package.base_status = "pending"

    def freeze(self, claim, record, config, entries):
        with self.db.sessions.begin() as session:
            kind = session.get(Package, claim.package_id).kind
        sources = {}
        if kind == "tv":
            movie, plan, sources = asset_descriptions(self, claim, record)
        else:
            movie = Movie.model_validate(record.details)
            existing_nfo = next(
                (
                    PurePosixPath(e.path).name
                    for e in entries
                    if not e.is_dir and e.path.lower().endswith(".nfo")
                ),
                None,
            )
            nfo_name = existing_nfo or PurePosixPath(record.media_path).with_suffix(".nfo").name
            plan = [
                (nfo_name, "nfo", True),
                ("poster.jpg", "poster", True),
                ("fanart.jpg", "fanart", True),
            ]
        plan += [
            (p["path"], "actor", bool(p["profile"]) or config.actor_policy == "strict")
            for p in record.cast
            if p.get("selected", True)
        ]
        plan_changed = False
        with self.db.sessions.begin() as session:
            task, package = self.queue._owned(session, claim)
            self.queue._current(session, task, package)
            required = [path for path, _, needed in plan if needed]
            if (
                package.base_required
                and package.base_required != required
                and any(
                    a.status == "remote_verified" and a.verified_version == package.context_version
                    for a in session.scalars(select(Asset).where(Asset.package_id == package.id))
                )
            ):
                invalidate_package(package, "asset_plan_changed")
                plan_changed = True
            package.base_required, package.base_status = required, "processing"
            for old in session.scalars(select(Asset).where(Asset.package_id == package.id)):
                old.required = False
            for path, kind, needed in plan:
                asset = session.scalar(
                    select(Asset).where(Asset.package_id == package.id, Asset.relative_path == path)
                )
                if not asset:
                    asset = Asset(package_id=package.id, relative_path=path, kind=kind)
                    session.add(asset)
                asset.required = needed
                asset.media_id = sources.get(path, {}).get("media_id")
                asset.season = sources.get(path, {}).get("season")
                if not needed and kind == "actor":
                    asset.status = "source_no_image"
            emit(session, "base_asset_plan_frozen", claim.task_id, required=len(required))
        if plan_changed:
            raise ProviderError("asset_plan_changed_submit_new_task")
        return movie, plan, sources

    async def retry_asset(self, claim):
        with self.db.sessions.begin() as session:
            step = session.scalar(
                select(TaskStep).where(
                    TaskStep.task_id == claim.task_id, TaskStep.stage == "asset_retry"
                )
            )
            asset = session.get(Asset, step.checkpoint.get("asset_id"))
            if (
                not asset
                or asset.package_id != claim.package_id
                or asset.kind not in {"nfo", "poster", "fanart", "actor", "season_poster", "thumb"}
            ):
                raise ProviderError("asset_retry_not_allowed")
            path = asset.relative_path
        return {"asset_id": asset.id, **await self.base(claim, only_path=path)}

    async def base(self, claim, *, only_path=None):
        config = self.configuration(claim)
        package, record = self.record(claim)
        if not record.details or record.match_status != "matched":
            raise ProviderError("metadata_not_matched")
        async with (
            self.storage_factory(config) as storage,
            self.metadata_factory(config, self.cache) as metadata,
        ):
            entries = await self.source(claim, storage, config)
            if only_path is None:
                movie, plan, sources = self.freeze(claim, record, config, entries)
            else:
                sources = {}
                if package.kind == "tv":
                    movie, _, sources = asset_descriptions(self, claim, record)
                else:
                    movie = Movie.model_validate(record.details)
                with self.db.sessions.begin() as session:
                    asset = session.scalar(
                        select(Asset).where(
                            Asset.package_id == package.id, Asset.relative_path == only_path
                        )
                    )
                    plan = [(asset.relative_path, asset.kind, asset.required)]
            people = [Person.model_validate(p) for p in record.cast]

            async def produce(path, kind):
                source = sources.get(path, {})
                if kind == "nfo":
                    return export_nfo(
                        source.get("model", movie),
                        people,
                        tag=source.get("tag", "movie"),
                        showtitle=movie.title,
                    )
                if kind == "actor":
                    person = next(p for p in record.cast if p["path"] == path)
                    if not person["profile"]:
                        raise ProviderError("actor_source_missing")
                    return validate_image(
                        await metadata.download_artwork(Artwork.model_validate(person["profile"])),
                        convert=True,
                    )
                alternatives = [
                    Artwork.model_validate(a)
                    for a in source.get("artwork", record.artwork)
                    if a["kind"] == kind
                ]
                if not alternatives and kind in {"season_poster", "thumb"}:
                    alternatives = await metadata.images(
                        movie.external_id,
                        "tv",
                        season=source["season"],
                        episode=getattr(source.get("model"), "episode", None),
                    )
                if not alternatives:
                    raise ProviderError("tmdb_" + kind + "_missing")
                last = None
                for art in alternatives[:5]:
                    try:
                        return validate_image(await metadata.download_artwork(art), convert=True)
                    except ProviderError as error:
                        last = error
                raise last

            # Finish independent assets even if one fails; retries reuse the completed set.
            failures = []
            for path, kind, required in plan:
                if not required and kind == "actor" and only_path is None:
                    continue
                source = sources.get(path, {})
                model = source.get("model", movie)
                try:
                    await self.upload(
                        claim,
                        storage,
                        package.remote_path,
                        path,
                        kind,
                        lambda p=path, k=kind: produce(p, k),
                        model.external_id,
                        model.imdb_id,
                        nfo_tag=source.get("tag", "movie"),
                        season=getattr(model, "season", None),
                        episode=getattr(model, "episode", None),
                    )
                except ProviderError as error:
                    if error.code in {
                        "lease_lost",
                        "context_changed_submit_new_task",
                        "configuration_changed_submit_new_task",
                        "pause_requested",
                    }:
                        raise
                    with self.db.sessions.begin() as session:
                        self.queue._owned(session, claim)
                        asset = session.scalar(
                            select(Asset).where(
                                Asset.package_id == package.id, Asset.relative_path == path
                            )
                        )
                        missing = not required and error.code == "tmdb_" + kind + "_missing"
                        asset.status, asset.error_code = (
                            ("source_no_image", None) if missing else ("failed", error.code)
                        )
                    if required or only_path is not None:
                        failures.append(error)
            await self.source(claim, storage, config)
            if failures:
                with self.db.sessions.begin() as session:
                    self.queue._owned(session, claim)
                    if only_path is None or plan[0][2]:
                        session.get(Package, package.id).base_status = "failed"
                raise failures[0]
            return {
                "required": len([p for p in plan if p[2]]),
                "media_content_reads": 0,
                "probe_calls": 0,
                "subtitle_provider_calls": 0,
                "archive_enabled": False,
            }

    async def upload(
        self,
        claim,
        storage,
        package_path,
        path,
        kind,
        produce,
        tmdb_id,
        imdb_id=None,
        *,
        nfo_tag="movie",
        season=None,
        episode=None,
    ):
        self.check(claim)
        with self.db.sessions.begin() as session:
            asset = session.scalar(
                select(Asset).where(
                    Asset.package_id == claim.package_id, Asset.relative_path == path
                )
            )
            if (
                asset.status == "remote_verified"
                and asset.verified_version == claim.context_version
                and self.cache.get(asset.cache_key)
            ):
                return
            expected, key, managed = asset.sha256, asset.cache_key, asset.managed
            source_tmdb_id = asset.source_tmdb_id
        remote = package_path + "/" + path
        try:
            entry = await storage.stat(remote)
        except ProviderError as error:
            if error.code != "storage_not_found":
                raise
            entry = None
        if entry:
            if managed and source_tmdb_id and source_tmdb_id != tmdb_id:
                raise ProviderError("existing_asset_identity_conflict")
            if entry.is_dir:
                raise ProviderError("existing_asset_conflict")
            body = await storage.read_small(
                remote, MAX_NFO_BYTES if kind == "nfo" else MAX_ASSET_BYTES
            )
            self.cache.put(body)  # immutable original/backup, before any interpretation
            if managed and expected and sha256(body) != expected:
                # A previously interrupted PUT must not adopt different bytes as success.
                raise ProviderError("upload_verification_failed")
            self.validate(
                body, kind, tmdb_id, imdb_id, nfo_tag=nfo_tag, season=season, episode=episode
            )
            existing = True
        else:
            body = self.cache.get(key) if source_tmdb_id in {None, tmdb_id} else None
            if body is None:
                body = await produce()
            self.validate(
                body, kind, tmdb_id, imdb_id, nfo_tag=nfo_tag, season=season, episode=episode
            )
            existing = False
            key = self.cache.put(body)
            with self.db.sessions.begin() as session:
                task, package = self.queue._owned(session, claim)
                self.queue._current(session, task, package)
                asset = session.scalar(
                    select(Asset).where(Asset.package_id == package.id, Asset.relative_path == path)
                )
                asset.cache_key, asset.sha256, asset.managed = key, sha256(body), True
                asset.status, asset.error_code = "cached", None
                asset.source_tmdb_id = tmdb_id
            self.check(claim)
            if path.startswith(".actors/"):
                await storage.mkdir(package_path + "/.actors")
            self.check(claim)
            uncertain_error = None
            try:
                await storage.put(remote, body)
            except ProviderError as error:
                if error.code not in {
                    "storage_timeout",
                    "storage_network_error",
                    "storage_conflict",
                }:
                    raise
                uncertain_error = error
                # No blind second PUT after an uncertain response; reconcile complete bytes.
            self.check(claim)
            try:
                returned = await storage.read_small(
                    remote, MAX_NFO_BYTES if kind == "nfo" else MAX_ASSET_BYTES
                )
            except ProviderError as error:
                if (
                    error.code == "storage_not_found"
                    and uncertain_error
                    and uncertain_error.retryable
                ):
                    raise ProviderError("upload_not_confirmed", retryable=True) from None
                raise
            if sha256(returned) != sha256(body):
                raise ProviderError("upload_verification_failed")
            entry = await storage.stat(remote)
        self.check(claim)
        key = self.cache.put(body)
        with self.db.sessions.begin() as session:
            task, package = self.queue._owned(session, claim)
            self.queue._current(session, task, package)
            asset = session.scalar(
                select(Asset).where(Asset.package_id == package.id, Asset.relative_path == path)
            )
            asset.cache_key, asset.sha256 = key, sha256(body)
            asset.status, asset.error_code = "remote_verified", None
            asset.remote_verified_at, asset.verified_version = time.time(), package.context_version
            asset.remote_snapshot = entry.model_dump() if entry else {}
            asset.source_tmdb_id = tmdb_id
            if existing and not managed:
                asset.managed = False
            emit(
                session,
                "asset_remote_verified",
                claim.task_id,
                asset_id=asset.id,
                kind=kind,
                reused=existing,
            )

    @staticmethod
    def validate(body, kind, tmdb_id, imdb_id=None, *, nfo_tag="movie", season=None, episode=None):
        if kind == "nfo":
            root = read_nfo(body, nfo_tag)
            if nfo_tag == "episodedetails" and (
                root.findtext("season"),
                root.findtext("episode"),
            ) != (str(season), str(episode)):
                raise ProviderError("tmdb_episode_identity_conflict")
            identifiers = nfo_ids(body, nfo_tag)
            valid = identifiers == {tmdb_id} or (
                not identifiers and imdb_id and imdb_ids(body, nfo_tag) == {imdb_id}
            )
            if not valid:
                raise ProviderError("tmdb_id_conflict")
        else:
            validate_image(body)
