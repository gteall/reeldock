"""Incremental, bounded PROPFIND discovery. Never requests file content."""

import time
from pathlib import PurePosixPath

from sqlalchemy import select

from reeldock.domain import ProviderError
from reeldock.episodes import episode_hint, parse_episode, season_folder
from reeldock.matching import TEMP_PATTERN, VIDEO_EXTENSIONS, parse_name
from reeldock.models import ArchiveIntent, Asset, Media, MovieRecord, Package, SeasonRecord
from reeldock.queue import revise_package

IGNORED = {"sample", "samples", "extras", "trailers"}


def media_entries(entries):
    return [
        e
        for e in entries
        if not e.is_dir
        and PurePosixPath(e.path).suffix.lower() in VIDEO_EXTENSIONS
        and not any(part.lower() in IGNORED for part in PurePosixPath(e.path).parts)
        and "sample" not in PurePosixPath(e.path).stem.lower().split(".")
    ]


def source_snapshot(entries):
    relevant = media_entries(entries) + [e for e in entries if TEMP_PATTERN.search(e.path)]
    return {"entries": [e.model_dump() for e in sorted(relevant, key=lambda e: e.path)]}


def package_reason(path, root, entries, videos, kind=None, mappings=None):
    if path == root:
        return "loose_media_requires_directory"
    if any(TEMP_PATTERN.search(e.path) for e in entries):
        return "download_in_progress"
    if not videos or any(e.size is None or e.size <= 0 for e in videos):
        return "media_size_unknown"
    tv = kind == "tv" or (kind is None and any(episode_hint(e.path) for e in videos))
    if tv:
        pairs = []
        for video in videos:
            try:
                # A multi-episode file remains unsupported even after manual editing.
                if mappings and video.path in mappings and mappings[video.path][2]:
                    try:
                        parse_episode(video.path, path)
                    except ProviderError as error:
                        if error.code == "multi_episode_file_unsupported":
                            raise
                    pair = mappings[video.path][:2]
                else:
                    pair = parse_episode(video.path, path)
                if None in pair:
                    return "episode_number_unknown"
                pairs.append(pair)
            except ProviderError as error:
                return error.code
        if len(set(pairs)) != len(pairs):
            return "duplicate_episode_mapping"
        return None
    if len(videos) != 1:
        return "multiple_media_requires_review"
    if any(
        e.is_dir
        and not PurePosixPath(e.path).name.startswith(".")
        and PurePosixPath(e.path).name.lower() not in IGNORED | {"subs", "subtitles"}
        for e in entries
    ):
        return "nested_package_requires_review"
    return None


async def collect(storage, root, check=lambda: None):
    todo, listing, count = [(root, 0)], {}, 0
    while todo:
        path, depth = todo.pop()
        check()
        entries = await storage.list(path)
        count += len(entries)
        if count > 20000 or len(listing) >= 2000:
            raise ProviderError("scan_limit_exceeded")
        listing[path] = entries
        for entry in entries:
            name = PurePosixPath(entry.path).name.lower()
            if (
                entry.is_dir
                and (not name.startswith(".") or name == ".actors")
                and name not in IGNORED
            ):
                if depth >= 8:
                    raise ProviderError("scan_depth_exceeded")
                todo.append((entry.path, depth + 1))
    return listing


def flatten(listing, root):
    return [
        e
        for path, entries in listing.items()
        if path == root or path.startswith(root + "/")
        for e in entries
    ]


class Scanner:
    def __init__(self, db):
        self.db = db

    async def scan(self, storage, config, revision, *, check=lambda: None, now=None):
        now = time.time() if now is None else now
        listing = await collect(storage, config.input_path, check)
        candidates, consumed = [], []
        for path, direct in sorted(listing.items(), key=lambda item: (item[0].count("/"), item[0])):
            if any(path.startswith(root + "/") for root in consumed):
                continue
            videos = media_entries(direct)
            tv = path != config.input_path and (
                any(PurePosixPath(e.path).name.lower() == "tvshow.nfo" for e in direct)
                or any(
                    e.is_dir and season_folder(PurePosixPath(e.path).name) is not None
                    for e in direct
                )
                or any(episode_hint(e.path) for e in videos)
            )
            entries = flatten(listing, path) if tv else direct + listing.get(path + "/.actors", [])
            if tv:
                videos = media_entries(entries)
                consumed.append(path)
            if videos:
                candidates.append((path, entries, videos, "tv" if tv else "movie"))
        check()
        with self.db.sessions.begin() as session:
            seen = set()
            for path, entries, videos, kind in candidates:
                package = session.scalar(select(Package).where(Package.remote_path == path))
                if not package:
                    package = Package(remote_path=path, kind=kind)
                    session.add(package)
                    session.flush()
                seen.add(package.id)
                if session.scalar(
                    select(ArchiveIntent.id).where(
                        ArchiveIntent.package_id == package.id, ArchiveIntent.status != "archived"
                    )
                ):
                    continue
                snapshot = source_snapshot(entries)
                snapshot["config_revision"] = revision
                snapshot["kind"] = kind
                changed = package.source_snapshot != snapshot or package.kind != kind
                package.kind = kind
                name_error = None
                try:
                    name = parse_name(
                        PurePosixPath(path).name
                        if kind == "tv"
                        else PurePosixPath(videos[0].path).name
                    )
                except ProviderError as error:
                    name, name_error = None, error.code
                record = session.get(MovieRecord, package.id)
                if record is None:
                    record = MovieRecord(
                        package_id=package.id,
                        title=name.title if name else PurePosixPath(path).name,
                        year=name.year if name else None,
                        stable_since=now,
                        last_seen=now,
                    )
                    session.add(record)
                if changed:
                    revise_package(session, package, source_snapshot=snapshot)
                    record.stable_since, record.match_status = now, "pending"
                    record.details, record.cast, record.artwork, record.candidates = {}, [], [], []
                    if name:
                        record.title, record.year = name.title, name.year
                record.last_seen = now
                record.media_path = videos[0].path if len(videos) == 1 else None
                current_paths = {e.path for e in videos}
                for old in session.scalars(select(Media).where(Media.package_id == package.id)):
                    if old.remote_path not in current_paths:
                        for asset in session.scalars(select(Asset).where(Asset.media_id == old.id)):
                            asset.media_id, asset.required = None, False
                        session.flush()
                        session.delete(old)
                mappings = {}
                for video in videos:
                    media = session.scalar(select(Media).where(Media.remote_path == video.path))
                    if not media:
                        media = Media(package_id=package.id, remote_path=video.path)
                        session.add(media)
                    elif media.package_id != package.id:
                        previous = session.get(Package, media.package_id)
                        if (
                            previous.lease_owner and (previous.lease_until or 0) > now
                        ) or session.scalar(
                            select(ArchiveIntent.id).where(
                                ArchiveIntent.package_id == previous.id,
                                ArchiveIntent.status != "archived",
                            )
                        ):
                            raise ProviderError("legacy_package_requires_recovery")
                        for asset in session.scalars(
                            select(Asset).where(Asset.media_id == media.id)
                        ):
                            asset.media_id, asset.required = None, False
                        revise_package(session, previous, source_snapshot={})
                        previous_record = session.get(MovieRecord, previous.id)
                        if previous_record:
                            previous_record.scan_status = "superseded"
                            previous_record.error_code = "series_package_regrouped"
                        media.package_id = package.id
                        media.metadata_version = media.subtitle_version = None
                        media.subtitle_status, media.subtitle_evidence = "pending", {}
                    media.size, media.etag = video.size, video.etag
                    if kind == "tv":
                        try:
                            pair = parse_episode(video.path, path)
                            if not media.manual_mapping:
                                media.season, media.episode = pair
                            media.mapping_status, media.error_code = "mapped", None
                        except ProviderError as error:
                            if (
                                not media.manual_mapping
                                or error.code == "multi_episode_file_unsupported"
                            ):
                                media.season = media.episode = None
                                media.mapping_status, media.error_code = "needs_review", error.code
                        mappings[video.path] = (media.season, media.episode, media.manual_mapping)
                        if (
                            media.season is not None
                            and session.get(SeasonRecord, (package.id, media.season)) is None
                        ):
                            session.add(SeasonRecord(package_id=package.id, number=media.season))
                    if changed:
                        media.metadata_version = media.subtitle_version = None
                        media.subtitle_status, media.subtitle_evidence = "pending", {}
                reason = name_error or package_reason(
                    path, config.input_path, entries, videos, kind, mappings
                )
                stable = not changed and now - record.stable_since >= config.stable_seconds
                record.scan_status = (
                    "needs_review" if reason else ("stable" if stable else "waiting_stable")
                )
                record.error_code = reason
                observed = {e.path: e for e in entries if not e.is_dir}
                for existing in session.scalars(
                    select(Asset).where(Asset.package_id == package.id)
                ):
                    full = path + "/" + existing.relative_path
                    if (
                        existing.kind
                        in {"nfo", "poster", "fanart", "actor", "season_poster", "thumb"}
                        and existing.status == "remote_verified"
                    ):
                        if full not in observed:
                            existing.status, package.base_status = "pending", "pending"
                        elif (
                            existing.remote_snapshot
                            and existing.remote_snapshot != observed[full].model_dump()
                        ):
                            existing.status, existing.managed, package.base_status = (
                                "existing_unverified",
                                False,
                                "pending",
                            )
                for full in observed:
                    relative = str(PurePosixPath(full).relative_to(path))
                    asset_kind = (
                        "nfo"
                        if relative.lower().endswith(".nfo")
                        else (
                            "actor"
                            if relative.startswith(".actors/") and relative.endswith(".jpg")
                            else {"poster.jpg": "poster", "fanart.jpg": "fanart"}.get(relative)
                        )
                    )
                    if asset_kind and not session.scalar(
                        select(Asset.id).where(
                            Asset.package_id == package.id, Asset.relative_path == relative
                        )
                    ):
                        session.add(
                            Asset(
                                package_id=package.id,
                                relative_path=relative,
                                kind=asset_kind,
                                required=False,
                                status="existing_unverified",
                            )
                        )
            for record in session.scalars(select(MovieRecord)):
                package = session.get(Package, record.package_id)
                if (
                    package.remote_path.startswith(config.input_path + "/")
                    and package.id not in seen
                    and record.scan_status != "superseded"
                    and not session.scalar(
                        select(ArchiveIntent.id).where(
                            ArchiveIntent.package_id == package.id,
                            ArchiveIntent.status != "archived",
                        )
                    )
                ):
                    if record.scan_status != "missing":
                        revise_package(session, package, source_snapshot={})
                    record.scan_status, record.error_code = "missing", "source_missing"
        return {"packages": len(candidates), "directories": len(listing), "media_content_reads": 0}
