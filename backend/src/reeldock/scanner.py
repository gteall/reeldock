"""Incremental PROPFIND discovery. This module never requests file content."""

import time
from pathlib import PurePosixPath

from sqlalchemy import select

from reeldock.domain import ProviderError
from reeldock.matching import EPISODE_PATTERN, TEMP_PATTERN, VIDEO_EXTENSIONS, parse_name
from reeldock.models import Asset, Media, MovieRecord, Package
from reeldock.queue import revise_package


def media_entries(entries):
    return [
        e
        for e in entries
        if not e.is_dir
        and PurePosixPath(e.path).suffix.lower() in VIDEO_EXTENSIONS
        and not any(
            part.lower() in {"sample", "samples", "extras", "trailers"}
            for part in PurePosixPath(e.path).parts
        )
        and "sample" not in PurePosixPath(e.path).stem.lower().split(".")
    ]


def source_snapshot(entries):
    relevant = media_entries(entries) + [e for e in entries if TEMP_PATTERN.search(e.path)]
    return {"entries": [e.model_dump() for e in sorted(relevant, key=lambda e: e.path)]}


def package_reason(path, root, entries, videos):
    if path == root:
        return "loose_media_requires_directory"
    if any(TEMP_PATTERN.search(e.path) for e in entries):
        return "download_in_progress"
    if len(videos) != 1:
        return "multiple_media_requires_review"
    if EPISODE_PATTERN.search(videos[0].path) or any(
        PurePosixPath(e.path).name.lower() == "tvshow.nfo" for e in entries
    ):
        return "tv_not_supported_p2"
    if any(
        e.is_dir
        and not PurePosixPath(e.path).name.startswith(".")
        and PurePosixPath(e.path).name.lower()
        not in {"subs", "subtitles", "extras", "samples", "sample", "trailers"}
        for e in entries
    ):
        return "nested_package_requires_review"
    if videos[0].size is None or videos[0].size <= 0:
        return "media_size_unknown"
    return None


class Scanner:
    def __init__(self, db):
        self.db = db

    async def scan(self, storage, config, revision, *, check=lambda: None, now=None):
        now = time.time() if now is None else now
        actor_listing = {}
        todo, listing, count = [(config.input_path, 0)], {}, 0
        while todo:
            path, depth = todo.pop()
            check()
            entries = await storage.list(path)
            count += len(entries)
            if count > 20000 or len(listing) >= 2000:
                raise ProviderError("scan_limit_exceeded")
            listing[path] = entries
            if any(e.is_dir and PurePosixPath(e.path).name == ".actors" for e in entries):
                actor_listing[path] = await storage.list(path + "/.actors")
            for e in entries:
                name = PurePosixPath(e.path).name.lower()
                if (
                    e.is_dir
                    and not name.startswith(".")
                    and name not in {"subs", "subtitles", "sample", "samples", "extras", "trailers"}
                ):
                    if depth >= 8:
                        raise ProviderError("scan_depth_exceeded")
                    todo.append((e.path, depth + 1))
        check()
        discovered = 0
        with self.db.sessions.begin() as session:
            seen = set()
            for path, entries in listing.items():
                videos = media_entries(entries)
                if not videos:
                    continue
                discovered += 1
                package = session.scalar(select(Package).where(Package.remote_path == path))
                if not package:
                    package = Package(remote_path=path, kind="movie")
                    session.add(package)
                    session.flush()
                seen.add(package.id)
                record = session.get(MovieRecord, package.id)
                snapshot = source_snapshot(entries)
                # Config identity forms part of the source context; never reuse another
                # server's assets just because its relative paths happen to coincide.
                snapshot["config_revision"] = revision
                changed = package.source_snapshot != snapshot
                name_error = None
                try:
                    name = parse_name(PurePosixPath(videos[0].path).name)
                except ProviderError as error:
                    name = None
                    name_error = error.code
                if record is None:
                    record = MovieRecord(
                        package_id=package.id,
                        title=name.title if name else PurePosixPath(videos[0].path).stem,
                        year=name.year if name else None,
                        stable_since=now,
                        last_seen=now,
                    )
                    session.add(record)
                if changed:
                    revise_package(session, package, source_snapshot=snapshot)
                    record.stable_since, record.match_status = now, "pending"
                    record.details, record.cast, record.artwork = {}, [], []
                    record.candidates = []
                    if name:
                        record.title, record.year = name.title, name.year
                record.last_seen, record.media_path = (
                    now,
                    videos[0].path if len(videos) == 1 else None,
                )
                reason = name_error or package_reason(path, config.input_path, entries, videos)
                stable = not changed and now - record.stable_since >= config.stable_seconds
                record.scan_status = (
                    "needs_review" if reason else ("stable" if stable else "waiting_stable")
                )
                record.error_code = reason
                current_media = {e.path for e in videos}
                for old_media in session.scalars(
                    select(Media).where(Media.package_id == package.id)
                ):
                    if old_media.remote_path not in current_media:
                        session.delete(old_media)
                for e in videos:
                    media = session.scalar(select(Media).where(Media.remote_path == e.path))
                    if not media:
                        media = Media(package_id=package.id, remote_path=e.path)
                        session.add(media)
                    media.size, media.etag = e.size, e.etag
                observed = entries + actor_listing.get(path, [])
                observed_paths = {e.path for e in observed if not e.is_dir}
                for existing in session.scalars(
                    select(Asset).where(Asset.package_id == package.id)
                ):
                    full_path = path + "/" + existing.relative_path
                    if existing.status == "remote_verified" and full_path not in observed_paths:
                        existing.status, package.base_status = "pending", "pending"
                for e in observed:
                    name = str(PurePosixPath(e.path).relative_to(path))
                    kind = (
                        "nfo"
                        if name.lower().endswith(".nfo")
                        else (
                            "actor"
                            if name.startswith(".actors/") and name.endswith(".jpg")
                            else {"poster.jpg": "poster", "fanart.jpg": "fanart"}.get(name)
                        )
                    )
                    if kind and not e.is_dir:
                        asset = session.scalar(
                            select(Asset).where(
                                Asset.package_id == package.id, Asset.relative_path == name
                            )
                        )
                        if (
                            asset
                            and asset.remote_snapshot
                            and asset.remote_snapshot != e.model_dump()
                        ):
                            if asset.status == "remote_verified":
                                asset.status, asset.managed, package.base_status = (
                                    "existing_unverified",
                                    False,
                                    "pending",
                                )
                        if not asset:
                            session.add(
                                Asset(
                                    package_id=package.id,
                                    relative_path=name,
                                    kind=kind,
                                    required=False,
                                    status="existing_unverified",
                                )
                            )
            for record in session.scalars(select(MovieRecord)):
                package = session.get(Package, record.package_id)
                if (
                    package.remote_path.startswith(config.input_path + "/")
                    and package.id not in seen
                ):
                    if record.scan_status != "missing":
                        revise_package(session, package, source_snapshot={})
                    record.scan_status, record.error_code = "missing", "source_missing"
        return {"packages": discovered, "directories": len(listing), "media_content_reads": 0}
