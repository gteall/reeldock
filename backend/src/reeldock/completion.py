"""In-place completion: verified base assets -> subtitles -> immutable manifest."""

import json
import time
from pathlib import PurePosixPath

from sqlalchemy import or_, select

from reeldock.cache import sha256
from reeldock.domain import ProviderError, Stage
from reeldock.models import Asset, Media, Package, TaskStep
from reeldock.probe import MediaProbe, validate_vobsub_pair
from reeldock.providers.assrt import AssrtProvider
from reeldock.queue import SUBTITLE_PASSED, emit, gate
from reeldock.subtitles import (
    BITMAP_SUBTITLE_CODECS,
    MAX_SUBTITLE,
    default_audio,
    matches_external,
    original_class,
    simplified_track,
    validate_subtitle,
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class CompletionPipeline:
    def __init__(
        self,
        movies,
        *,
        probe=None,
        subtitle_factory=AssrtProvider,
        manual_subtitle_factory=AssrtProvider,
    ):
        self.movies, self.db, self.queue = movies, movies.db, movies.queue
        self.probe = probe or MediaProbe()
        self.subtitle_factory = subtitle_factory
        self.manual_subtitle_factory = manual_subtitle_factory

    async def apply_subtitle(self, claim):
        config = self.movies.configuration(claim)
        package, _ = self.movies.record(claim)
        with self.db.sessions.begin() as session:
            self.queue._owned(session, claim)
            step = session.scalar(
                select(TaskStep).where(
                    TaskStep.task_id == claim.task_id, TaskStep.stage == "subtitle_apply"
                )
            )
            selection = step.checkpoint
            media = session.get(Media, selection.get("media_id"))
            if not media or media.package_id != package.id:
                raise ProviderError("episode_not_found")
        saved = media.probe_evidence
        if saved.get("context_version") != claim.context_version:
            raise ProviderError("subtitle_duration_unknown")
        duration = saved.get("probe", {}).get("duration")
        async with self.movies.storage_factory(config) as storage:
            await self.base_readback(claim, storage)
            await self.movies.source(claim, storage, config)
            await self.verify_media(claim, storage, media)
            async with self.manual_subtitle_factory(config) as provider:
                raw = await provider.download(
                    selection["candidate_id"], selection["file_index"], selection["file_name"]
                )
            self.movies.cache.put(raw)
            extension = PurePosixPath(selection["file_name"]).suffix.lower().lstrip(".")
            body, evidence = validate_subtitle(raw, extension, duration)
            parent = PurePosixPath(media.remote_path).parent.relative_to(package.remote_path)
            relative = str(
                parent
                / (
                    PurePosixPath(media.remote_path).stem
                    + ".assrt."
                    + sha256(body)[:12]
                    + "."
                    + extension
                )
            )
            await self.verify_media(claim, storage, media)
            await self.save_asset(claim, storage, relative, "subtitle", body, media_id=media.id)
            with self.db.sessions.begin() as session:
                self.queue._owned(session, claim)
                for old in session.scalars(
                    select(Asset).where(
                        Asset.media_id == media.id,
                        Asset.kind == "subtitle",
                        Asset.relative_path != relative,
                    )
                ):
                    old.required = False
                for old in session.scalars(
                    select(Asset).where(
                        Asset.package_id == package.id,
                        Asset.kind == "manifest",
                    )
                ):
                    old.required = False
                session.get(Package, package.id).manifest_version = None
            selected_evidence = {
                "source": "assrt",
                "candidate_id": selection["candidate_id"],
                "file_name": selection["file_name"],
                "keyword": selection["keyword"],
                "download": evidence,
                "path": relative,
                "manual": True,
            }
            self.media_result(media.id, claim, "downloaded_verified", evidence=selected_evidence)
            with self.db.sessions.begin() as session:
                items = list(session.scalars(select(Media).where(Media.package_id == package.id)))
                remaining = sum(
                    item.subtitle_status not in SUBTITLE_PASSED
                    or item.subtitle_version != claim.context_version
                    for item in items
                )
                results = {
                    item.id: {"status": item.subtitle_status, **item.subtitle_evidence}
                    for item in items
                }
            if remaining:
                self.result(claim, "failed", "subtitle_manual_remaining", {"media": results})
            elif len(items) == 1:
                self.result(claim, "downloaded_verified", evidence=selected_evidence)
            else:
                self.result(claim, "all_media_verified", evidence={"media": results})
            await self.movies.source(claim, storage, config)
        if not remaining:
            await self.manifest(claim)
        return {"media_id": media.id, "source": "assrt", "path": relative, "remaining": remaining}

    async def exists(self, storage, path):
        try:
            return await storage.stat(path)
        except ProviderError as error:
            if error.code == "storage_not_found":
                return None
            raise

    def check(self, claim, stage=Stage.SUBTITLE):
        self.movies.check(claim)
        with self.db.sessions.begin() as session:
            package = session.get(Package, claim.package_id)
            gate(session, package, stage)

    async def base_readback(self, claim, storage):
        self.check(claim)
        with self.db.sessions.begin() as session:
            package = session.get(Package, claim.package_id)
            assets = list(
                session.scalars(
                    select(Asset).where(Asset.package_id == package.id, Asset.required.is_(True))
                )
            )
        for asset in assets:
            if asset.relative_path not in package.base_required:
                continue
            self.check(claim)
            body = await storage.read_small(
                package.remote_path + "/" + asset.relative_path, 20 * 1024 * 1024
            )
            if sha256(body) != asset.sha256:
                with self.db.sessions.begin() as session:
                    self.queue._owned(session, claim)
                    current = session.get(Asset, asset.id)
                    current.status, current.error_code = "failed", "upload_verification_failed"
                    session.get(Package, package.id).base_status = "failed"
                raise ProviderError("base_assets_not_remote_verified")
        self.check(claim)
        with self.db.sessions.begin() as session:
            self.queue._owned(session, claim)
            emit(
                session,
                "base_gate_readback_passed",
                claim.task_id,
                required=len(package.base_required),
            )

    def result(self, claim, status, reason=None, evidence=None, *, media_id=None):
        self.check(claim)
        with self.db.sessions.begin() as session:
            task, package = self.queue._owned(session, claim)
            self.queue._current(session, task, package)
            row = session.get(Media, media_id) if media_id else package
            row.subtitle_status, row.subtitle_reason = status, reason
            row.subtitle_version = package.context_version
            row.subtitle_evidence = evidence or {}
            if media_id:
                row.original_language = package.original_language
                row.error_code = reason if status in {"failed", "needs_review"} else None
            emit(
                session,
                "subtitle_policy_result",
                claim.task_id,
                status=status,
                reason=reason,
                media_id=media_id,
            )
        return {"status": status, **(evidence or {})}

    def media_result(self, media_id, claim, status, reason=None, evidence=None):
        return self.result(claim, status, reason, evidence, media_id=media_id)

    async def verify_media(self, claim, storage, media):
        self.check(claim)
        current = await storage.stat(media.remote_path)
        if current.size != media.size or (media.etag and current.etag != media.etag):
            raise ProviderError("source_changed_scan_again")

    async def inventory(self, claim, storage, root):
        """Whole package, including ordinary attachments; never reads video contents."""
        with self.db.sessions.begin() as session:
            controls = set(
                session.scalars(
                    select(Asset.relative_path).where(
                        Asset.package_id == claim.package_id, Asset.kind == "manifest"
                    )
                )
            )
        result, todo = [], [(root, 0)]
        while todo:
            path, depth = todo.pop()
            self.movies.check(claim)
            for entry in await storage.list(path):
                relative = str(PurePosixPath(entry.path).relative_to(root))
                # Our immutable manifests are control records, not source attachments.
                if relative in controls:
                    continue
                if len(result) >= 20000 or depth > 8:
                    raise ProviderError("manifest_inventory_limit")
                result.append(
                    {
                        "path": relative,
                        "is_dir": entry.is_dir,
                        "size": None if entry.is_dir else entry.size,
                        "etag": None if entry.is_dir else entry.etag,
                        "modified": None if entry.is_dir else entry.modified,
                    }
                )
                if entry.is_dir:
                    todo.append((entry.path, depth + 1))
        return sorted(result, key=lambda e: e["path"])

    async def save_asset(self, claim, storage, path, kind, body, *, media_id=None):
        """Immutable create, intent-before-PUT, full reconciliation after lost response."""
        self.check(claim)
        with self.db.sessions.begin() as session:
            self.queue._owned(session, claim)
            package = session.get(Package, claim.package_id)
            asset = session.scalar(
                select(Asset).where(Asset.package_id == package.id, Asset.relative_path == path)
            )
            if not asset:
                asset = Asset(package_id=package.id, relative_path=path, kind=kind)
                session.add(asset)
            if asset.sha256 and asset.managed and asset.sha256 != sha256(body):
                raise ProviderError("existing_asset_conflict")
            asset.required, asset.managed = True, True
            asset.media_id = media_id
            asset.sha256, asset.cache_key = sha256(body), self.movies.cache.put(body)
            asset.status, asset.error_code = "cached", None
            remote, asset_id = package.remote_path + "/" + path, asset.id
            session.flush()
            asset_id = asset.id
        try:
            try:
                entry = await storage.stat(remote)
            except ProviderError as error:
                if error.code != "storage_not_found":
                    raise
                entry = None
            if entry is None:
                parent = str(PurePosixPath(remote).parent)
                if parent != package.remote_path:
                    await storage.mkdir(parent)
                self.check(claim)
                try:
                    await storage.put(remote, body)
                except ProviderError as error:
                    if error.code not in {
                        "storage_timeout",
                        "storage_network_error",
                        "storage_conflict",
                    }:
                        raise
            self.check(claim)
            returned = await storage.read_small(remote, MAX_SUBTITLE)
            if sha256(returned) != sha256(body):
                raise ProviderError("upload_verification_failed")
            entry = await storage.stat(remote)
            self.check(claim)
            with self.db.sessions.begin() as session:
                self.queue._owned(session, claim)
                asset = session.get(Asset, asset_id)
                asset.status, asset.error_code = "remote_verified", None
                asset.verified_version, asset.remote_verified_at = (
                    claim.context_version,
                    time.time(),
                )
                asset.remote_snapshot = entry.model_dump()
                emit(session, "asset_remote_verified", claim.task_id, kind=kind, asset_id=asset_id)
        except ProviderError as error:
            with self.db.sessions.begin() as session:
                self.queue._owned(session, claim)
                asset = session.get(Asset, asset_id)
                asset.status, asset.error_code = "failed", error.code
            raise

    async def subtitle(self, claim):
        config = self.movies.configuration(claim)
        self.check(claim)  # before constructing a probe or subtitle Provider
        package, record = self.movies.record(claim)
        async with self.movies.storage_factory(config) as storage:
            await self.base_readback(claim, storage)
            await self.movies.source(claim, storage, config)
            branch = original_class(package.original_language, config.chinese_languages)
            with self.db.sessions.begin() as session:
                self.queue._owned(session, claim)
                media = list(
                    session.scalars(
                        select(Media)
                        .where(Media.package_id == package.id)
                        .order_by(Media.season, Media.episode, Media.remote_path)
                    )
                )
                for old in session.scalars(
                    select(Asset).where(
                        Asset.package_id == package.id, Asset.kind.in_(["subtitle", "manifest"])
                    )
                ):
                    if (
                        old.kind == "manifest"
                        or branch != "foreign"
                        or old.verified_version != claim.context_version
                    ):
                        old.required = False
            if not media:
                raise ProviderError("media_mapping_ambiguous")
            if branch in {"chinese", "unknown"}:
                status = "skipped_tmdb_chinese" if branch == "chinese" else "needs_review"
                reason = (
                    "tmdb_original_language_chinese"
                    if branch == "chinese"
                    else "tmdb_original_language_unknown"
                )
                evidence = {"actual_audio": "not_probed"}
                for item in media:
                    self.media_result(item.id, claim, status, reason, evidence)
                result = self.result(claim, status, reason, evidence)
                if branch == "unknown":
                    raise ProviderError(reason)
                return result
            failures, results = [], {}
            for item in media:
                try:
                    reused = await self.reuse_media(claim, storage, item, package)
                    results[item.id] = reused or await self.foreign(
                        claim, storage, config, package, record, item
                    )
                except ProviderError as error:
                    if error.code in {
                        "lease_lost",
                        "pause_requested",
                        "context_changed_submit_new_task",
                        "configuration_changed_submit_new_task",
                    }:
                        raise
                    review = error.code in {
                        "default_audio_ambiguous",
                        "default_audio_unknown",
                        "embedded_subtitle_ambiguous",
                        "subtitle_duration_unknown",
                        "subtitle_timeline_mismatch",
                        "source_changed_scan_again",
                    }
                    self.media_result(
                        item.id, claim, "needs_review" if review else "failed", error.code
                    )
                    failures.append(error)
            if failures:
                first = failures[0]
                review = first.code in {
                    "default_audio_ambiguous",
                    "default_audio_unknown",
                    "embedded_subtitle_ambiguous",
                    "subtitle_duration_unknown",
                    "subtitle_timeline_mismatch",
                    "source_changed_scan_again",
                }
                self.result(
                    claim, "needs_review" if review else "failed", first.code, {"media": results}
                )
                raise first
            await self.movies.source(claim, storage, config)
            if len(media) == 1:
                result = results[media[0].id]
                return self.result(
                    claim,
                    result["status"],
                    evidence={k: v for k, v in result.items() if k != "status"},
                )
            return self.result(claim, "all_media_verified", evidence={"media": results})

    async def reuse_media(self, claim, storage, media, package):
        if (
            media.subtitle_version != claim.context_version
            or media.subtitle_status not in SUBTITLE_PASSED
            or media.original_language != package.original_language
        ):
            return None
        await self.verify_media(claim, storage, media)
        if media.subtitle_status in {"external_verified", "downloaded_verified"}:
            with self.db.sessions.begin() as session:
                assets = list(
                    session.scalars(
                        select(Asset).where(
                            Asset.media_id == media.id,
                            Asset.required.is_(True),
                            Asset.kind == "subtitle",
                        )
                    )
                )
            if not assets:
                return None
            for asset in assets:
                if (
                    asset.status != "remote_verified"
                    or asset.verified_version != claim.context_version
                ):
                    return None
                body = await storage.read_small(
                    package.remote_path + "/" + asset.relative_path, 20 * 1024 * 1024
                )
                if sha256(body) != asset.sha256:
                    raise ProviderError("upload_verification_failed")
        return {"status": media.subtitle_status, **media.subtitle_evidence}

    async def foreign(self, claim, storage, config, package, record, media):
        await self.verify_media(claim, storage, media)
        # Reuse evidence only for this exact source/context and policy revision.
        saved = media.probe_evidence
        cached = saved.get("context_version") == claim.context_version
        async with self.probe.session(storage, media, config, lambda: self.check(claim)) as probe:
            data = saved["probe"] if cached else await probe.probe()
            with self.db.sessions.begin() as session:
                self.queue._owned(session, claim)
                row = session.get(Media, media.id)
                row.probe_status = "probed"
                row.probe_evidence = {"context_version": claim.context_version, "probe": data}
            audio = default_audio(data)
            with self.db.sessions.begin() as session:
                self.queue._owned(session, claim)
                row = session.get(Media, media.id)
                row.probe_status = "probed"
                row.probe_evidence = {
                    "context_version": claim.context_version,
                    "probe": data,
                    "audio": audio,
                }
            await self.verify_media(claim, storage, media)
            if audio["language"] == "zh":
                return self.media_result(
                    media.id, claim, "default_audio_chinese", evidence={"audio": audio}
                )
            # A valid existing external already satisfies policy; avoid an unnecessary
            # full embedded extraction consuming the shared media byte/time budget.
            entries = await self.inventory(claim, storage, package.remote_path)
            failures = []
            for entry in entries:
                remote = package.remote_path + "/" + entry["path"]
                if entry["is_dir"] or not matches_external(remote, media.remote_path):
                    continue
                extension = PurePosixPath(remote).suffix.lstrip(".").lower()
                pair = []
                try:
                    self.check(claim)
                    body = await storage.read_small(
                        remote, MAX_SUBTITLE if extension != "sub" else 20 * 1024 * 1024
                    )
                    if extension in {"idx", "sub"}:
                        idx_path = str(PurePosixPath(remote).with_suffix(".idx"))
                        sub_path = str(PurePosixPath(remote).with_suffix(".sub"))
                        if not await self.exists(storage, idx_path) or not await self.exists(
                            storage, sub_path
                        ):
                            raise ProviderError("subtitle_pair_incomplete")
                        idx = (
                            body
                            if extension == "idx"
                            else await storage.read_small(idx_path, MAX_SUBTITLE)
                        )
                        sub = (
                            body
                            if extension == "sub"
                            else await storage.read_small(sub_path, 20 * 1024 * 1024)
                        )
                        evidence = await validate_vobsub_pair(
                            self.probe,
                            idx,
                            sub,
                            data["duration"],
                            lambda: self.check(claim),
                            config,
                        )
                        pair = [(idx_path, idx), (sub_path, sub)]
                    else:
                        _, evidence = validate_subtitle(body, extension, data["duration"])
                    await self.verify_media(claim, storage, media)
                except ProviderError as error:
                    failures.append(error.code)
                    await self.external_failure(claim, entry["path"], error.code)
                    continue
                if pair:
                    for pair_path, pair_body in pair:
                        await self.remember_external(
                            claim, storage, pair_path, pair_body, package.remote_path, media.id
                        )
                    return self.media_result(
                        media.id,
                        claim,
                        "external_verified",
                        evidence={"audio": audio, "external": evidence, "path": entry["path"]},
                    )
                await self.remember_external(
                    claim, storage, remote, body, package.remote_path, media.id
                )
                return self.media_result(
                    media.id,
                    claim,
                    "external_verified",
                    evidence={"audio": audio, "external": evidence, "path": entry["path"]},
                )
            for stream in sorted(data["streams"], key=lambda item: not item.get("default")):
                if stream["type"] != "subtitle" or not simplified_track(stream):
                    continue
                if stream["codec"] in BITMAP_SUBTITLE_CODECS:
                    # Bitmap streams cannot be extracted as text without OCR. Trust
                    # explicit Hans labels on non-forced dialogue tracks and record
                    # the source of this decision instead of claiming text validation.
                    evidence = {
                        "verification": "stream_metadata",
                        "codec": stream["codec"],
                        "language": stream.get("language"),
                        "title": stream.get("title"),
                    }
                elif stream["codec"] in {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}:
                    try:
                        content = await probe.extract(stream["index"])
                        _, evidence = validate_subtitle(content, "srt", data["duration"], full=True)
                    except ProviderError as error:
                        if error.code in {
                            "embedded_subtitle_incomplete",
                            "embedded_subtitle_not_simplified",
                            "subtitle_no_dialogue",
                            "subtitle_invalid_timing",
                        }:
                            continue
                        raise
                    evidence["verification"] = "parsed_text"
                else:
                    continue
                await self.verify_media(claim, storage, media)
                return self.media_result(
                    media.id,
                    claim,
                    "embedded_zh_hans",
                    evidence={
                        "audio": audio,
                        "embedded": evidence,
                        "stream_index": stream["index"],
                    },
                )
        await self.verify_media(claim, storage, media)
        keyword = PurePosixPath(media.remote_path).stem
        with self.db.sessions.begin() as session:
            self.queue._owned(session, claim)
            row = session.get(Media, media.id)
            row.probe_evidence = {
                **row.probe_evidence,
                "subtitle_search": {"source": "assrt", "keyword": keyword},
            }
        attempted = False
        async with self.subtitle_factory(config) as provider:
            self.check(claim)
            async for candidate in provider.automatic_candidates(
                keyword,
                {
                    "filename": PurePosixPath(media.remote_path).name,
                    "season": media.season,
                    "episode": media.episode,
                },
            ):
                attempted = True
                self.check(claim)
                try:
                    raw = await provider.download(
                        candidate["candidate_id"], candidate["index"], candidate["name"]
                    )
                    self.movies.cache.put(raw)
                    body, evidence = validate_subtitle(raw, candidate["format"], data["duration"])
                except ProviderError as error:
                    if error.retryable or error.code in {
                        "assrt_token_required",
                        "assrt_token_invalid",
                    }:
                        raise
                    failures.append(error.code)
                    continue
                # Never label an unclassified download as simplified Chinese.
                filename = (
                    PurePosixPath(media.remote_path).stem
                    + ".assrt."
                    + sha256(body)[:12]
                    + "."
                    + candidate["format"]
                )
                parent = PurePosixPath(media.remote_path).parent.relative_to(package.remote_path)
                path = str(parent / filename)
                await self.save_asset(claim, storage, path, "subtitle", body, media_id=media.id)
                await self.verify_media(claim, storage, media)
                await self.movies.source(claim, storage, config)
                return self.media_result(
                    media.id,
                    claim,
                    "downloaded_verified",
                    evidence={
                        "audio": audio,
                        "download": evidence,
                        "path": path,
                        "source": "assrt",
                        "keyword": keyword,
                        "candidate_id": candidate["candidate_id"],
                        "file_name": candidate["name"],
                        "manual": False,
                    },
                )
        if not attempted:
            raise ProviderError("subtitle_no_match")
        raise ProviderError(failures[-1] if failures else "subtitle_no_valid_download")

    async def external_failure(self, claim, path, code):
        self.check(claim)
        with self.db.sessions.begin() as session:
            self.queue._owned(session, claim)
            asset = session.scalar(
                select(Asset).where(
                    Asset.package_id == claim.package_id, Asset.relative_path == path
                )
            )
            if asset is None:
                asset = Asset(
                    package_id=claim.package_id, relative_path=path, kind="subtitle", required=False
                )
                session.add(asset)
            asset.status, asset.error_code, asset.required = "failed", code, False

    async def remember_external(self, claim, storage, remote, body, root, media_id):
        self.check(claim)
        snapshot = (await storage.stat(remote)).model_dump()
        path = str(PurePosixPath(remote).relative_to(root))
        with self.db.sessions.begin() as session:
            self.queue._owned(session, claim)
            asset = session.scalar(
                select(Asset).where(
                    Asset.package_id == claim.package_id, Asset.relative_path == path
                )
            )
            if asset is None:
                asset = Asset(
                    package_id=claim.package_id, relative_path=path, kind="subtitle", managed=False
                )
                session.add(asset)
            asset.required, asset.status, asset.error_code = True, "remote_verified", None
            asset.media_id = media_id
            asset.sha256, asset.cache_key = sha256(body), self.movies.cache.put(body)
            asset.remote_verified_at, asset.verified_version = time.time(), claim.context_version
            asset.remote_snapshot = snapshot

    def media_manifest(self, claim, package):
        with self.db.sessions.begin() as session:
            return [
                {
                    "id": m.id,
                    "path": str(PurePosixPath(m.remote_path).relative_to(package.remote_path)),
                    "season": m.season,
                    "episode": m.episode,
                    "tmdb_id": m.details.get("external_id"),
                    "original_language": package.original_language,
                    "subtitle_status": m.subtitle_status,
                    "subtitle_evidence": m.subtitle_evidence,
                }
                for m in session.scalars(
                    select(Media).where(Media.package_id == package.id).order_by(Media.remote_path)
                )
            ]

    async def manifest(self, claim):
        config = self.movies.configuration(claim)
        self.check(claim, Stage.MANIFEST)
        package, _ = self.movies.record(claim)
        async with self.movies.storage_factory(config) as storage:
            await self.base_readback(claim, storage)
            await self.movies.source(claim, storage, config)
            await storage.mkdir(package.remote_path + "/.reeldock")
            inventory = await self.inventory(claim, storage, package.remote_path)
            with self.db.sessions.begin() as session:
                self.queue._owned(session, claim)
                assets = list(
                    session.scalars(
                        select(Asset).where(
                            Asset.package_id == package.id,
                            or_(
                                Asset.required.is_(True),
                                (Asset.kind.in_(["season_poster", "thumb"]))
                                & (Asset.status == "remote_verified")
                                & (Asset.verified_version == claim.context_version),
                            ),
                            Asset.kind != "manifest",
                        )
                    )
                )
            for asset in assets:
                self.check(claim, Stage.MANIFEST)
                if (
                    sha256(
                        await storage.read_small(
                            package.remote_path + "/" + asset.relative_path, 20 * 1024 * 1024
                        )
                    )
                    != asset.sha256
                ):
                    raise ProviderError("upload_verification_failed")
            body = canonical(
                {
                    "schema": 1,
                    "package_id": package.id,
                    "context_version": claim.context_version,
                    "policy_version": package.policy_version,
                    "tmdb_id": package.tmdb_id,
                    "original_language": package.original_language,
                    "subtitle_status": package.subtitle_status,
                    "subtitle_evidence": package.subtitle_evidence,
                    "media": self.media_manifest(claim, package),
                    "source": package.remote_path,
                    "inventory": inventory,
                    "assets": [
                        {
                            "path": a.relative_path,
                            "kind": a.kind,
                            "sha256": a.sha256,
                            "required": a.required,
                        }
                        for a in sorted(assets, key=lambda a: a.relative_path)
                    ],
                }
            )
            path = f".reeldock/manifest-v{claim.context_version}-{sha256(body)[:12]}.json"
            with self.db.sessions.begin() as session:
                self.queue._owned(session, claim)
                for old in session.scalars(
                    select(Asset).where(Asset.package_id == package.id, Asset.kind == "manifest")
                ):
                    old.required = False
            await self.save_asset(claim, storage, path, "manifest", body)
            if inventory != await self.inventory(claim, storage, package.remote_path):
                raise ProviderError("source_changed_before_manifest")
            with self.db.sessions.begin() as session:
                self.queue._owned(session, claim)
                session.get(Package, package.id).manifest_version = claim.context_version
        return {"sha256": sha256(body), "entries": len(inventory)}
