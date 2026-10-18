"""TV-specific identities and asset descriptions; upload/subtitles/MOVE stay shared."""

from pathlib import PurePosixPath

from sqlalchemy import select

from reeldock.domain import Episode, ProviderError, Series
from reeldock.exporter import MAX_NFO_BYTES, nfo_ids
from reeldock.matching import ParsedName, automatic_candidate, explicit_ids, rank_candidates
from reeldock.models import Media, MovieRecord, Package, SeasonRecord
from reeldock.queue import revise_package


def art_data(art):
    return art.model_dump(exclude={"url"})


async def match_series(pipeline, claim):
    config = pipeline.configuration(claim)
    package, record = pipeline.record(claim)
    async with (
        pipeline.storage_factory(config) as storage,
        pipeline.metadata_factory(config, pipeline.cache) as metadata,
    ):
        entries = await pipeline.source(claim, storage, config)
        if record.match_status != "matched" or not record.details:
            ids = explicit_ids(package.remote_path)
            root_nfo = package.remote_path + "/tvshow.nfo"
            for entry in entries:
                if entry.path == root_nfo and not entry.is_dir:
                    body = await storage.read_small(root_nfo, MAX_NFO_BYTES)
                    pipeline._remember_existing(claim, "tvshow.nfo", body)
                    identifiers = nfo_ids(body, "tvshow")
                    if not identifiers:
                        raise ProviderError("existing_nfo_id_missing")
                    ids |= identifiers
            if len(ids) > 1 or (record.manual_id and ids and record.manual_id not in ids):
                raise ProviderError("tmdb_id_conflict")
            chosen, candidates = record.manual_id or next(iter(ids), None), record.candidates
            if chosen is None:
                candidates = rank_candidates(
                    ParsedName(record.title, record.year, None),
                    await metadata.search(record.title, "tv", record.year),
                )
                chosen = automatic_candidate(candidates)
                if not chosen:
                    with pipeline.db.sessions.begin() as session:
                        pipeline.queue._owned(session, claim)
                        row = session.get(MovieRecord, package.id)
                        row.candidates, row.match_status, row.error_code = (
                            candidates,
                            "needs_review",
                            "tmdb_match_needs_review",
                        )
                    raise ProviderError("tmdb_match_needs_review")
            series = await metadata.series_details(chosen)
            if series.external_id != chosen:
                raise ProviderError("tmdb_id_conflict")
            cast = pipeline.cast(await metadata.credits(chosen, "tv"), config)
            artwork = await metadata.images(chosen, "tv")
            await pipeline.source(claim, storage, config)
            changed = False
            with pipeline.db.sessions.begin() as session:
                pipeline.queue._owned(session, claim)
                current, row = (
                    session.get(Package, package.id),
                    session.get(MovieRecord, package.id),
                )
                if current.tmdb_id and (
                    current.tmdb_id != chosen
                    or (
                        (current.original_language is not None or current.base_required)
                        and current.original_language != series.original_language
                    )
                ):
                    revise_package(
                        session, current, tmdb_id=chosen, original_language=series.original_language
                    )
                    changed = True
                current.tmdb_id, current.original_language, current.policy_version = (
                    chosen,
                    series.original_language,
                    config.policy_version,
                )
                row.details, row.cast, row.artwork = (
                    series.model_dump(),
                    cast,
                    [art_data(a) for a in artwork],
                )
                row.match_status, row.candidates, row.error_code = "matched", candidates, None
            if changed:
                raise ProviderError("metadata_changed_submit_new_task")
        else:
            series = Series.model_validate(record.details)
        with pipeline.db.sessions.begin() as session:
            media = list(
                session.scalars(
                    select(Media)
                    .where(Media.package_id == package.id)
                    .order_by(Media.season, Media.episode)
                )
            )
        failures = []
        for season in sorted({m.season for m in media}):
            pipeline.check(claim)
            with pipeline.db.sessions.begin() as session:
                saved = session.get(SeasonRecord, (package.id, season))
            if saved is None or saved.metadata_version != claim.context_version:
                data = await metadata.season_details(series.external_id, season)
                if data.series_id != series.external_id or data.season != season:
                    raise ProviderError("tmdb_episode_identity_conflict")
                with pipeline.db.sessions.begin() as session:
                    pipeline.queue._owned(session, claim)
                    saved = session.get(SeasonRecord, (package.id, season))
                    if saved is None:
                        saved = SeasonRecord(package_id=package.id, number=season)
                        session.add(saved)
                    saved.details, saved.artwork, saved.metadata_version = (
                        data.model_dump(),
                        [],
                        claim.context_version,
                    )
        for item in media:
            pipeline.check(claim)
            if (
                item.metadata_version == claim.context_version
                and item.details
                and item.original_language == series.original_language
            ):
                continue
            try:
                episode = await metadata.episode_details(
                    series.external_id, item.season, item.episode
                )
                if episode.series_id != series.external_id or (episode.season, episode.episode) != (
                    item.season,
                    item.episode,
                ):
                    raise ProviderError("tmdb_episode_identity_conflict")
                # The series field is authoritative, including a missing original_language.
                episode.original_language = series.original_language
                with pipeline.db.sessions.begin() as session:
                    pipeline.queue._owned(session, claim)
                    row = session.get(Media, item.id)
                    row.details, row.artwork, row.original_language = (
                        episode.model_dump(),
                        [],
                        series.original_language,
                    )
                    row.metadata_version, row.error_code = claim.context_version, None
            except ProviderError as error:
                if error.code in {
                    "lease_lost",
                    "pause_requested",
                    "context_changed_submit_new_task",
                    "configuration_changed_submit_new_task",
                }:
                    raise
                with pipeline.db.sessions.begin() as session:
                    pipeline.queue._owned(session, claim)
                    session.get(Media, item.id).error_code = error.code
                failures.append(error)
        await pipeline.source(claim, storage, config)
        if failures:
            raise failures[0]
        return {
            "tmdb_id": series.external_id,
            "original_language": series.original_language,
            "episodes": len(media),
        }


def asset_descriptions(pipeline, claim, record):
    series = Series.model_validate(record.details)
    plan = [
        ("tvshow.nfo", "nfo", True),
        ("poster.jpg", "poster", True),
        ("fanart.jpg", "fanart", True),
    ]
    sources = {
        path: {"model": series, "tag": "tvshow", "artwork": record.artwork} for path, _, _ in plan
    }
    with pipeline.db.sessions.begin() as session:
        media = list(
            session.scalars(
                select(Media)
                .where(Media.package_id == claim.package_id)
                .order_by(Media.season, Media.episode)
            )
        )
        seasons = list(
            session.scalars(select(SeasonRecord).where(SeasonRecord.package_id == claim.package_id))
        )
        root = session.get(Package, claim.package_id).remote_path
    if not media or any(
        m.metadata_version != claim.context_version or not m.details for m in media
    ):
        raise ProviderError("metadata_not_matched")
    for item in media:
        episode = Episode.model_validate(item.details)
        # Package paths are authoritative; do not trust provider paths.
        relative = PurePosixPath(item.remote_path).relative_to(root)
        nfo = str(relative.with_suffix(".nfo"))
        thumb = str(relative.with_name(relative.stem + "-thumb.jpg"))
        plan.extend([(nfo, "nfo", True), (thumb, "thumb", False)])
        sources[nfo] = sources[thumb] = {
            "model": episode,
            "tag": "episodedetails",
            "media_id": item.id,
            "season": item.season,
            "artwork": item.artwork,
        }
    for season in seasons:
        if season.number not in {m.season for m in media}:
            continue
        path = (
            "season-specials-poster.jpg"
            if season.number == 0
            else f"season{season.number:02}-poster.jpg"
        )
        plan.append((path, "season_poster", False))
        sources[path] = {
            "model": series,
            "tag": "tvshow",
            "season": season.number,
            "artwork": season.artwork,
        }
    return series, plan, sources
