"""Reviewable, source-bound deletion of one media package's sidecar metadata."""

import secrets
from pathlib import PurePosixPath

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from reeldock.cache import sha256
from reeldock.domain import ProviderError, normalize_path
from reeldock.models import Asset, Media, MovieRecord, Package, SeasonRecord, TaskStep
from reeldock.scanner import collect, flatten, source_snapshot
from reeldock.sidecars import metadata_file


async def reset_inventory(storage, package, check=lambda: None):
    entries = flatten(await collect(storage, package.remote_path, check), package.remote_path)
    if source_snapshot(entries)["entries"] != package.source_snapshot.get("entries"):
        raise ProviderError("source_changed_scan_again")
    controls = package.remote_path + "/.reeldock"
    if any(entry.path == controls and entry.is_dir for entry in entries):
        entries += await storage.list(controls)
    return entries


async def deletion_plan(storage, package, media):
    entries = await reset_inventory(storage, package)
    videos = [
        str(PurePosixPath(item.remote_path).relative_to(package.remote_path)) for item in media
    ]
    files = []
    for entry in entries:
        if entry.is_dir:
            continue
        relative = str(PurePosixPath(entry.path).relative_to(package.remote_path))
        if not metadata_file(relative, videos, kind=package.kind):
            continue
        if len(files) >= 2000:
            raise ProviderError("metadata_delete_limit")
        raw = await storage.read_small(entry.path, 20 * 1024 * 1024)
        files.append(
            {
                "path": relative,
                "size": entry.size,
                "etag": entry.etag,
                "modified": entry.modified,
                "sha256": sha256(raw),
            }
        )
    return sorted(files, key=lambda item: item["path"])


def clear_records(session, package, config_revision):
    from reeldock.queue import invalidate_package

    invalidate_package(package, "metadata_deleted")
    package.tmdb_id, package.original_language, package.base_required = None, None, []
    package.source_snapshot = {**package.source_snapshot, "config_revision": config_revision}
    session.execute(delete(Asset).where(Asset.package_id == package.id))
    record = session.get(MovieRecord, package.id)
    record.details, record.cast, record.artwork, record.candidates = {}, [], [], []
    record.match_status, record.manual_id, record.error_code = "pending", None, None
    for item in session.scalars(select(Media).where(Media.package_id == package.id)):
        item.details, item.artwork = {}, []
        item.metadata_version = item.subtitle_version = item.original_language = None
        item.subtitle_status, item.subtitle_evidence, item.error_code = "pending", {}, None
        item.probe_status, item.probe_evidence = "not_started", {}
    for season in session.scalars(
        select(SeasonRecord).where(SeasonRecord.package_id == package.id)
    ):
        season.details, season.artwork, season.metadata_version = {}, [], None


async def reset_metadata(pipeline, claim):
    config = pipeline.configuration(claim)
    with pipeline.db.sessions.begin() as session:
        package = session.get(Package, claim.package_id)
        step = session.scalar(
            select(TaskStep).where(
                TaskStep.task_id == claim.task_id, TaskStep.stage == "metadata_reset"
            )
        )
        saved = step.checkpoint
        media = list(session.scalars(select(Media).where(Media.package_id == package.id)))
    if config.root_for(package.remote_path) is None:
        raise ProviderError("package_outside_scrape_directories")
    done = set(saved.get("deleted", []))
    async with pipeline.storage_factory(config) as storage:
        await reset_inventory(storage, package, lambda: pipeline.check(claim))
        videos = [
            str(PurePosixPath(item.remote_path).relative_to(package.remote_path)) for item in media
        ]
        for item in saved["files"]:
            pipeline.check(claim)
            relative = item["path"]
            if (
                relative != normalize_path("/" + relative).lstrip("/")
                or relative in videos
                or not metadata_file(relative, videos, kind=package.kind)
            ):
                raise ProviderError("metadata_delete_rejected")
            remote = package.remote_path + "/" + relative
            try:
                current = await storage.stat(remote)
            except ProviderError as error:
                if error.code != "storage_not_found":
                    raise
                current = None
            if relative in done:
                if current is not None:
                    raise ProviderError("metadata_changed_refresh_preview")
                continue
            if current is not None:
                if current.is_dir or current.size != item["size"] or current.etag != item["etag"]:
                    raise ProviderError("metadata_changed_refresh_preview")
                raw = await storage.read_small(remote, 20 * 1024 * 1024)
                if sha256(raw) != item["sha256"]:
                    raise ProviderError("metadata_changed_refresh_preview")
                pipeline.check(claim)
                try:
                    await storage.delete(remote, etag=item["etag"])
                except ProviderError as error:
                    if error.code not in {"storage_timeout", "storage_network_error"}:
                        raise
                try:
                    await storage.stat(remote)
                except ProviderError as error:
                    if error.code != "storage_not_found":
                        raise
                else:
                    raise ProviderError("metadata_delete_not_confirmed", retryable=True)
            done.add(relative)
            with pipeline.db.sessions.begin() as session:
                pipeline.queue._owned(session, claim)
                row = session.get(TaskStep, step.id)
                row.checkpoint = {**saved, "deleted": sorted(done)}
        remaining = await reset_inventory(storage, package, lambda: pipeline.check(claim))
        if any(
            not e.is_dir
            and metadata_file(
                str(PurePosixPath(e.path).relative_to(package.remote_path)),
                videos,
                kind=package.kind,
            )
            for e in remaining
        ):
            raise ProviderError("metadata_changed_refresh_preview")
    return {"deleted": len(done), "video_files_preserved": len(media)}


class ResetMetadata(BaseModel):
    preview_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9:_-]+$")


def register_metadata_routes(app, mutation):
    def context(package_id):
        config, revision = app.state.store.load()
        with app.state.db.sessions.begin() as session:
            package = session.get(Package, package_id)
            record = session.get(MovieRecord, package_id)
            if not package or not record:
                raise HTTPException(404, "package_not_found")
            if not config or config.root_for(package.remote_path) is None:
                raise HTTPException(409, "package_outside_scrape_directories")
            if record.scan_status in {"missing", "superseded"}:
                raise HTTPException(409, "source_missing")
            media = list(session.scalars(select(Media).where(Media.package_id == package.id)))
        return config, revision, package, media

    @app.post("/api/packages/{package_id}/metadata/preview", dependencies=[Depends(mutation)])
    async def preview_metadata(package_id: str):
        config, revision, package, media = context(package_id)
        try:
            async with app.state.worker.storage_factory(config) as storage:
                files = await deletion_plan(storage, package, media)
        except ProviderError as error:
            raise HTTPException(409, error.code) from None
        preview_id = secrets.token_urlsafe(24)
        app.state.worker.movies.cache.save_response(
            "metadata_reset",
            {"id": preview_id},
            {
                "package_id": package.id,
                "context_version": package.context_version,
                "config_revision": revision,
                "files": files,
            },
            ttl=1800,
        )
        return {"preview_id": preview_id, "files": files, "video_files_preserved": len(media)}

    @app.post(
        "/api/packages/{package_id}/metadata/delete",
        status_code=202,
        dependencies=[Depends(mutation)],
    )
    def delete_metadata(package_id: str, body: ResetMetadata):
        _, revision, package, _ = context(package_id)
        saved = app.state.worker.movies.cache.response("metadata_reset", {"id": body.preview_id})
        if (
            not saved
            or saved["package_id"] != package.id
            or saved["config_revision"] != revision
            or saved["context_version"] != package.context_version
        ):
            raise HTTPException(409, "metadata_preview_expired")
        try:
            task_id = app.state.queue.submit(
                "metadata_reset",
                package.remote_path,
                body.idempotency_key,
                revision,
                checkpoint={"files": saved["files"]},
                expected_context_version=package.context_version,
            )
        except ProviderError as error:
            raise HTTPException(409, error.code) from None
        except ValueError:
            raise HTTPException(409, "idempotency_key_conflict") from None
        return {"task_id": task_id}
