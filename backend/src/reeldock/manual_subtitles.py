"""Authenticated manual subtitle search; short-lived results contain no download URLs."""

import secrets
from pathlib import PurePosixPath

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from reeldock.domain import ProviderError
from reeldock.models import Media, MovieRecord, Package


class SubtitleSearch(BaseModel):
    media_id: str = Field(min_length=1, max_length=64)
    keyword: str | None = Field(default=None, max_length=512)
    expected_context_version: int = Field(ge=1)


class SubtitleFiles(BaseModel):
    search_id: str = Field(min_length=1, max_length=64)
    candidate_id: str = Field(pattern=r"^[0-9]{1,12}$")


class SubtitleApply(SubtitleFiles):
    file_index: int = Field(ge=0, le=199)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9:_-]+$")


def register_subtitle_routes(app, mutation):
    def context(package_id, media_id, version):
        config, revision = app.state.store.load()
        if not config:
            raise HTTPException(409, "configuration_required")
        if not config.assrt_token or not config.assrt_token.get_secret_value():
            raise HTTPException(409, "assrt_token_required")
        with app.state.db.sessions.begin() as session:
            package = session.get(Package, package_id)
            media = session.get(Media, media_id)
            record = session.get(MovieRecord, package_id)
            if not package or not record or not media or media.package_id != package.id:
                raise HTTPException(404, "episode_not_found")
            if package.context_version != version:
                raise HTTPException(409, "context_changed_submit_new_task")
            if config.root_for(package.remote_path) is None:
                raise HTTPException(409, "package_outside_scrape_directories")
            if record.scan_status in {"missing", "superseded"}:
                raise HTTPException(409, "source_missing")
        return config, revision, package, media

    def cache():
        return app.state.worker.movies.cache

    def search_result(package_id, search_id, candidate_id):
        saved = cache().response("manual_subtitle_search", {"search_id": search_id})
        if not saved or saved["package_id"] != package_id:
            raise HTTPException(409, "subtitle_search_expired")
        if candidate_id not in {item["id"] for item in saved["items"]}:
            raise HTTPException(404, "subtitle_candidate_expired")
        values = context(package_id, saved["media_id"], saved["context_version"])
        if values[1] != saved["config_revision"]:
            raise HTTPException(409, "subtitle_search_expired")
        return saved, values

    @app.post("/api/packages/{package_id}/subtitles/search", dependencies=[Depends(mutation)])
    async def search_subtitles(package_id: str, body: SubtitleSearch):
        config, revision, package, media = context(
            package_id, body.media_id, body.expected_context_version
        )
        keyword = (
            body.keyword if body.keyword is not None else PurePosixPath(media.remote_path).stem
        ).strip()
        if len(keyword) < 3 or any(ord(char) < 32 for char in keyword):
            raise HTTPException(422, "subtitle_keyword_too_short")
        try:
            async with app.state.worker.completion.manual_subtitle_factory(config) as provider:
                items = await provider.search(keyword)
        except ProviderError as error:
            raise HTTPException(502, error.code) from None
        search_id = secrets.token_urlsafe(24)
        saved = {
            "package_id": package.id,
            "media_id": media.id,
            "context_version": package.context_version,
            "config_revision": revision,
            "keyword": keyword,
            "items": items,
        }
        cache().save_response("manual_subtitle_search", {"search_id": search_id}, saved, ttl=1800)
        return {"search_id": search_id, "keyword": keyword, "items": items}

    @app.post("/api/packages/{package_id}/subtitles/files", dependencies=[Depends(mutation)])
    async def subtitle_files(package_id: str, body: SubtitleFiles):
        saved, (config, _, _, _) = search_result(package_id, body.search_id, body.candidate_id)
        try:
            async with app.state.worker.completion.manual_subtitle_factory(config) as provider:
                rows = await provider.files(body.candidate_id)
        except ProviderError as error:
            raise HTTPException(502, error.code) from None
        files = [{key: value for key, value in row.items() if key != "url"} for row in rows]
        cache().save_response(
            "manual_subtitle_files",
            {"search_id": body.search_id, "candidate_id": body.candidate_id},
            {"files": files},
            ttl=1800,
        )
        return {"files": files, "keyword": saved["keyword"]}

    @app.post(
        "/api/packages/{package_id}/subtitles/apply",
        status_code=202,
        dependencies=[Depends(mutation)],
    )
    def apply_subtitle(package_id: str, body: SubtitleApply):
        saved, (_, revision, package, media) = search_result(
            package_id, body.search_id, body.candidate_id
        )
        files = cache().response(
            "manual_subtitle_files",
            {"search_id": body.search_id, "candidate_id": body.candidate_id},
        )
        selected = next(
            (item for item in (files or {}).get("files", []) if item["index"] == body.file_index),
            None,
        )
        if selected is None:
            raise HTTPException(409, "subtitle_search_expired")
        selection = {
            "media_id": media.id,
            "candidate_id": body.candidate_id,
            "file_index": selected["index"],
            "file_name": selected["name"],
            "keyword": saved["keyword"],
        }
        try:
            task_id = app.state.queue.submit(
                "subtitle_apply",
                package.remote_path,
                body.idempotency_key,
                revision,
                checkpoint=selection,
                expected_context_version=saved["context_version"],
            )
        except ProviderError as error:
            raise HTTPException(409, error.code) from None
        except ValueError:
            raise HTTPException(409, "idempotency_key_conflict") from None
        return {"task_id": task_id}
