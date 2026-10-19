import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select, text

from reeldock import __version__
from reeldock.database import Database
from reeldock.domain import ConfigUpdate, ProviderError, normalize_path
from reeldock.episodes import parse_episode
from reeldock.events import stream_events
from reeldock.manual_subtitles import register_subtitle_routes
from reeldock.metadata_reset import register_metadata_routes
from reeldock.models import (
    Admin,
    ArchiveIntent,
    Asset,
    AuthSession,
    Configuration,
    Event,
    LoginLimit,
    Media,
    MovieRecord,
    Package,
    SeasonRecord,
    Task,
    TaskStep,
)
from reeldock.queue import Queue, invalidate_package, revise_package
from reeldock.runtime import Runtime
from reeldock.security import (
    SettingsStore,
    Vault,
    configure_logging,
    digest,
    passwords,
    random_token,
)
from reeldock.state import inspect_state, state_lock
from reeldock.subtitles import decode
from reeldock.worker import Worker

COOKIE = "reeldock_session"


class Login(BaseModel):
    username: str = Field(max_length=128)
    password: SecretStr = Field(max_length=1024)


class SubmitTask(BaseModel):
    kind: Literal["movie_base", "package_pipeline"] = "movie_base"
    package_path: str = Field(max_length=2048)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9:_-]+$")


class ConnectionCheck(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9:_-]+$")


def create_app(
    runtime: Runtime | None = None,
    *,
    storage_factory=None,
    metadata_factory=None,
    probe=None,
    subtitle_factory=None,
    manual_subtitle_factory=None,
) -> FastAPI:
    runtime = runtime or Runtime()

    @asynccontextmanager
    async def lifespan(app):
        configure_logging()
        os.umask(0o077)
        runtime.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with state_lock(runtime.data_dir):
            inspect_state(runtime.database_path, runtime.data_dir / "master.key")
            db = Database(runtime.database_path)
            try:
                db.migrate()
                vault = Vault(runtime.data_dir)
                store = SettingsStore(db, vault)
                store.load()
                queue = Queue(db, lease_seconds=runtime.lease_seconds)
                worker = Worker(
                    queue,
                    store,
                    runtime,
                    **({"storage_factory": storage_factory} if storage_factory else {}),
                    **({"metadata_factory": metadata_factory} if metadata_factory else {}),
                    **({"probe": probe} if probe else {}),
                    **({"subtitle_factory": subtitle_factory} if subtitle_factory else {}),
                    **(
                        {"manual_subtitle_factory": manual_subtitle_factory}
                        if manual_subtitle_factory
                        else {}
                    ),
                )
                app.state.db, app.state.store, app.state.queue = db, store, queue
                app.state.worker = worker
                app.state.dummy_password_hash = passwords.hash(random_token())
                if runtime.worker_enabled:
                    await worker.start()
                try:
                    yield
                finally:
                    await worker.stop()
            finally:
                db.close()

    app = FastAPI(
        title="ReelDock · 影坞",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_, error):
        return JSONResponse(
            status_code=422,
            content={
                "detail": "invalid_request",
                "errors": [
                    {"loc": list(item["loc"]), "message": item["msg"]} for item in error.errors()
                ],
            },
        )

    @app.exception_handler(Exception)
    async def internal_error(_, error):
        # Exception contents may contain remote URLs / DB bind values, so never serialize them.
        return JSONResponse(status_code=500, content={"detail": "internal_error"})

    def check_origin(request: Request):
        if request.headers.get("origin") not in runtime.allowed_origins:
            raise HTTPException(403, "request_origin_denied")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "request_origin_denied")

    def authenticated(request: Request):
        token = request.cookies.get(COOKIE, "")
        with app.state.db.sessions.begin() as session:
            row = session.get(AuthSession, digest(token)) if token else None
            if not row or row.expires_at <= time.time():
                raise HTTPException(401, "login_required")
            return row

    def mutation(request: Request, auth=Depends(authenticated)):
        check_origin(request)
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not secrets.compare_digest(digest(csrf), auth.csrf_hash):
            raise HTTPException(403, "csrf_check_failed")
        return auth

    register_subtitle_routes(app, mutation)
    register_metadata_routes(app, mutation)

    @app.get("/api/health")
    def health():
        with app.state.db.sessions.begin() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__, "phase": "P5", "mode": "in_place"}

    @app.get("/api/auth/status")
    def auth_status():
        with app.state.db.sessions.begin() as session:
            return {"admin_initialized": session.get(Admin, 1) is not None}

    @app.post("/api/auth/login", dependencies=[Depends(check_origin)])
    async def login(body: Login, request: Request, response: Response):
        bucket = digest(request.client.host if request.client else "unknown")
        now = time.time()
        with app.state.db.sessions.begin() as session:
            limit = session.get(LoginLimit, bucket)
            if not limit:
                limit = LoginLimit(bucket=bucket, attempts=0, reset_at=now + 60)
                session.add(limit)
            if limit.reset_at <= now:
                limit.attempts, limit.reset_at = 0, now + 60
            if limit.attempts >= 5:
                raise HTTPException(429, "login_rate_limited")
            limit.attempts += 1
            admin = session.get(Admin, 1)
            hashed = admin.password_hash if admin else app.state.dummy_password_hash
        valid = await asyncio.to_thread(passwords.verify, body.password.get_secret_value(), hashed)
        if (
            not valid
            or not admin
            or not secrets.compare_digest(digest(body.username), digest(admin.username))
        ):
            raise HTTPException(401, "invalid_credentials")
        token = random_token()
        csrf = digest("csrf:" + token)
        with app.state.db.sessions.begin() as session:
            current_admin = session.get(Admin, 1)
            if not current_admin or current_admin.password_hash != hashed:
                # A local reset may have completed while password verification was running.
                raise HTTPException(401, "invalid_credentials")
            old = request.cookies.get(COOKIE)
            if old:
                previous = session.get(AuthSession, digest(old))
                if previous:
                    session.delete(previous)
            # Expired sessions are garbage collected at successful login.
            for expired in session.scalars(
                select(AuthSession).where(AuthSession.expires_at <= now)
            ):
                session.delete(expired)
            session.add(
                AuthSession(
                    token_hash=digest(token), csrf_hash=digest(csrf), expires_at=now + 86400
                )
            )
            limit = session.get(LoginLimit, bucket)
            limit.attempts = 0
            session.add(Event(code="administrator_logged_in", details={}))
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            secure=runtime.secure_cookie,
            samesite="strict",
            max_age=86400,
            path="/",
        )
        return {"username": admin.username, "csrf_token": csrf}

    @app.get("/api/auth/session", dependencies=[Depends(authenticated)])
    def get_session(request: Request):
        with app.state.db.sessions.begin() as session:
            admin = session.get(Admin, 1)
            return {
                "username": admin.username,
                "csrf_token": digest("csrf:" + request.cookies[COOKIE]),
            }

    @app.post("/api/auth/logout", dependencies=[Depends(mutation)])
    def logout(request: Request, response: Response):
        with app.state.db.sessions.begin() as session:
            row = session.get(AuthSession, digest(request.cookies[COOKIE]))
            session.delete(row)
        response.delete_cookie(COOKIE, path="/")
        return {"logged_out": True}

    @app.get("/api/config", dependencies=[Depends(authenticated)])
    def config():
        return app.state.store.public()

    @app.put("/api/config", dependencies=[Depends(mutation)])
    def save_config(body: ConfigUpdate):
        try:
            app.state.store.save(body)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        return app.state.store.public()

    @app.post("/api/connection-check", status_code=202, dependencies=[Depends(mutation)])
    def connection_check(body: ConnectionCheck):
        config, revision = app.state.store.load()
        if config is None:
            raise HTTPException(409, "configuration_required")
        try:
            task_id = app.state.queue.submit(
                "connection_check", ":connection", body.idempotency_key, revision
            )
        except ValueError:
            raise HTTPException(409, "idempotency_key_conflict") from None
        return {"task_id": task_id}

    @app.post("/api/tasks", status_code=202, dependencies=[Depends(mutation)])
    def submit_task(body: SubmitTask):
        config, revision = app.state.store.load()
        if config is None:
            raise HTTPException(409, "configuration_required")
        try:
            path = normalize_path(body.package_path)
        except ValueError:
            raise HTTPException(422, "invalid_package_path") from None
        if config.root_for(path) is None:
            raise HTTPException(422, "package_outside_scrape_directories")
        try:
            task_id = app.state.queue.submit(body.kind, path, body.idempotency_key, revision)
        except ProviderError as error:
            raise HTTPException(409, error.code) from None
        except ValueError:
            raise HTTPException(409, "idempotency_key_conflict") from None
        return {"task_id": task_id, "implementation": body.kind}

    @app.post("/api/scan", status_code=202, dependencies=[Depends(mutation)])
    def scan(body: ConnectionCheck):
        config, revision = app.state.store.load()
        if not config:
            raise HTTPException(409, "configuration_required")
        try:
            task_id = app.state.queue.submit("scan", ":scan", body.idempotency_key, revision)
        except ValueError:
            raise HTTPException(409, "idempotency_key_conflict") from None
        return {"task_id": task_id}

    def media_view(item, package):
        current = item.subtitle_version == package.context_version
        probed = (
            item.probe_evidence.get("context_version") == package.context_version
            and item.subtitle_status != "skipped_tmdb_chinese"
        )
        return {
            "id": item.id,
            "path": item.remote_path,
            "subtitle_search_keyword": PurePosixPath(item.remote_path).stem,
            "season": item.season,
            "episode": item.episode,
            "title": item.details.get("title") or PurePosixPath(item.remote_path).stem,
            "mapping_status": item.mapping_status,
            "manual_mapping": item.manual_mapping,
            "tmdb_id": item.details.get("external_id")
            if item.metadata_version == package.context_version
            else None,
            "original_language": package.original_language,
            "subtitle_status": item.subtitle_status if current else "pending",
            "subtitle_reason": item.subtitle_reason if current else None,
            "subtitle_evidence": item.subtitle_evidence if current else {},
            "probe_status": item.probe_status if probed else "not_started",
            "probe_evidence": item.probe_evidence if probed else {},
            "error_code": item.error_code,
        }

    def movie_view(session, row):
        package = session.get(Package, row.package_id)
        assets = list(
            session.scalars(
                select(Asset).where(Asset.package_id == package.id).order_by(Asset.relative_path)
            )
        )
        latest = session.scalar(
            select(Task)
            .where(Task.package_id == package.id, Task.kind != "asset_retry")
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        media = session.scalar(select(Media).where(Media.package_id == package.id))
        intent = session.scalar(
            select(ArchiveIntent)
            .where(ArchiveIntent.package_id == package.id)
            .order_by(ArchiveIntent.created_at.desc())
            .limit(1)
        )
        current_probe = (
            media
            and media.probe_evidence.get("context_version") == package.context_version
            and package.subtitle_status != "skipped_tmdb_chinese"
        )
        config_row = session.get(Configuration, 1)
        config = app.state.store.vault.open(config_row.encrypted) if config_row else None
        return {
            "id": package.id,
            "actor_images_enabled": bool(config and config.scrape_actor_images),
            "manual_subtitles_enabled": bool(config and config.assrt_token),
            "scrape_completed": bool(
                package.manifest_version == package.context_version
                and package.base_status == "remote_verified"
                and package.subtitle_version == package.context_version
                and any(
                    asset.kind == "manifest"
                    and asset.required
                    and asset.status == "remote_verified"
                    and asset.verified_version == package.context_version
                    for asset in assets
                )
            ),
            "kind": package.kind,
            "context_version": package.context_version,
            "lease_active": bool(package.lease_owner and (package.lease_until or 0) > time.time()),
            "media": [
                media_view(m, package)
                for m in session.scalars(
                    select(Media)
                    .where(Media.package_id == package.id)
                    .order_by(Media.season, Media.episode, Media.remote_path)
                )
            ],
            "seasons": [
                {
                    "number": season.number,
                    "title": season.details.get("title")
                    or ("Specials" if season.number == 0 else f"Season {season.number}"),
                }
                for season in session.scalars(
                    select(SeasonRecord)
                    .where(SeasonRecord.package_id == package.id)
                    .order_by(SeasonRecord.number)
                )
            ],
            "path": package.remote_path,
            "title": row.details.get("title") or row.title,
            "year": row.details.get("year") or row.year,
            "media_path": row.media_path,
            "scan_status": row.scan_status,
            "stable_since": row.stable_since,
            "last_seen": row.last_seen,
            "match_status": row.match_status,
            "tmdb_id": package.tmdb_id,
            "manual_id": row.manual_id,
            "search_query": row.search_query,
            "match_evidence": row.match_evidence
            if row.match_evidence.get("context_version") == package.context_version
            else {},
            "original_language": package.original_language,
            "candidates": row.candidates,
            "metadata": {
                "source": row.details.get("source"),
                "original_title": row.details.get("original_title", ""),
                "overview": row.details.get("overview", ""),
                "tagline": row.details.get("tagline", ""),
                "release_date": row.details.get("release_date", ""),
                "genres": row.details.get("genres", []),
                "rating": row.details.get("rating"),
                "votes": row.details.get("votes", 0),
                "directors": row.details.get("directors", []),
                "writers": row.details.get("writers", []),
                "imdb_id": row.details.get("imdb_id"),
            },
            "base_status": package.base_status,
            "subtitle_status": package.subtitle_status,
            "subtitle_reason": package.subtitle_reason,
            "subtitle_evidence": package.subtitle_evidence,
            "probe_status": media.probe_status if current_probe else "not_started",
            "probe_evidence": media.probe_evidence if current_probe else {},
            "archive_status": package.archive_status,
            "archive_intent": {
                "id": intent.id,
                "status": intent.status,
                "error_code": intent.error_code,
                "failed_paths": intent.snapshot.get("move_failure", {}).get("failed_paths", []),
            }
            if intent
            else None,
            "archive_enabled": False,
            "error_code": row.error_code
            or (
                latest.error_code
                if latest and latest.context_version == package.context_version
                else None
            ),
            "task_id": latest.id
            if latest and latest.context_version == package.context_version
            else None,
            "task_status": latest.status
            if latest and latest.context_version == package.context_version
            else None,
            "cast": [
                {
                    "id": p["external_id"],
                    "name": p["name"],
                    "role": p.get("role"),
                    "path": p["path"],
                    "source_has_image": bool(p["profile"]),
                    "selected": p.get("selected", True),
                }
                for p in row.cast
            ],
            "assets": [
                {
                    "id": a.id,
                    "media_id": a.media_id,
                    "season": a.season,
                    "path": a.relative_path,
                    "kind": a.kind,
                    "required": a.required,
                    "status": a.status,
                    "error_code": a.error_code,
                    "managed": a.managed,
                    "sha256": a.sha256,
                    "remote_verified_at": a.remote_verified_at,
                    "preview": "/api/assets/" + a.id + "/preview"
                    if a.cache_key
                    and (
                        a.kind != "subtitle"
                        or PurePosixPath(a.relative_path).suffix.lower() in {".srt", ".ass", ".ssa"}
                    )
                    else None,
                }
                for a in assets
            ],
        }

    @app.get("/api/packages", dependencies=[Depends(authenticated)])
    @app.get("/api/movies", dependencies=[Depends(authenticated)])
    def movies():
        with app.state.db.sessions.begin() as session:
            return {
                "items": [
                    movie_view(session, row)
                    for row in session.scalars(select(MovieRecord).order_by(MovieRecord.title))
                ]
            }

    @app.get("/api/packages/{package_id}", dependencies=[Depends(authenticated)])
    @app.get("/api/movies/{package_id}", dependencies=[Depends(authenticated)])
    def movie_detail(package_id: str):
        with app.state.db.sessions.begin() as session:
            row = session.get(MovieRecord, package_id)
            if not row:
                raise HTTPException(404, "movie_not_found")
            return movie_view(session, row)

    class ManualMatch(BaseModel):
        tmdb_id: str = Field(pattern=r"^[1-9]\d{0,9}$")

    @app.put("/api/packages/{package_id}/match", dependencies=[Depends(mutation)])
    @app.put("/api/movies/{package_id}/match", dependencies=[Depends(mutation)])
    def manual_match(package_id: str, body: ManualMatch):
        with app.state.db.sessions.begin() as session:
            row, package = session.get(MovieRecord, package_id), session.get(Package, package_id)
            if not row or not package:
                raise HTTPException(404, "movie_not_found")
            if package.lease_owner and (package.lease_until or 0) > time.time():
                raise HTTPException(409, "package_busy_pause_first")
            if body.tmdb_id != package.tmdb_id:
                revise_package(session, package, tmdb_id=body.tmdb_id, original_language=None)
                package.base_required = []
                row.details, row.cast, row.artwork = {}, [], []
            row.manual_id, row.match_status, row.error_code = body.tmdb_id, "pending", None
            row.candidates, row.match_evidence = [], {}
            session.add(Event(code="manual_tmdb_id_selected", details={"package_id": package_id}))
        return {"accepted": True}

    class MetadataSearchQuery(BaseModel):
        query: str | None = Field(default=None, max_length=200)
        expected_context_version: int = Field(ge=1)

    @app.put("/api/packages/{package_id}/search-query", dependencies=[Depends(mutation)])
    def save_search_query(package_id: str, body: MetadataSearchQuery):
        query = " ".join((body.query or "").split()) or None
        if query and not any(char.isalnum() for char in query):
            raise HTTPException(422, "metadata_search_query_invalid")
        with app.state.db.sessions.begin() as session:
            package, row = session.get(Package, package_id), session.get(MovieRecord, package_id)
            if not package or not row:
                raise HTTPException(404, "movie_not_found")
            if body.expected_context_version != package.context_version:
                raise HTTPException(409, "context_changed_submit_new_task")
            if package.lease_owner and (package.lease_until or 0) > time.time():
                raise HTTPException(409, "package_busy_pause_first")
            if row.scan_status in {"missing", "superseded"}:
                raise HTTPException(409, "package_not_ready")
            invalidate_package(package, "metadata_search_changed")
            package.tmdb_id, package.original_language, package.base_required = None, None, []
            row.search_query, row.match_evidence, row.candidates = query, {}, []
            row.manual_id, row.match_status, row.error_code = None, "pending", None
            row.details, row.cast, row.artwork = {}, [], []
            session.add(
                Event(code="metadata_search_query_saved", details={"package_id": package_id})
            )
        return {"accepted": True}

    class EpisodeMapping(BaseModel):
        season: int = Field(ge=0, le=99)
        episode: int = Field(ge=1, le=999)
        expected_context_version: int = Field(ge=1)

    @app.put("/api/packages/{package_id}/episodes/{media_id}", dependencies=[Depends(mutation)])
    def correct_episode(package_id: str, media_id: str, body: EpisodeMapping):
        with app.state.db.sessions.begin() as session:
            package, item = session.get(Package, package_id), session.get(Media, media_id)
            if not package or not item or item.package_id != package_id or package.kind != "tv":
                raise HTTPException(404, "episode_not_found")
            if body.expected_context_version != package.context_version:
                raise HTTPException(409, "context_changed_submit_new_task")
            if package.lease_owner and (package.lease_until or 0) > time.time():
                raise HTTPException(409, "package_busy_pause_first")
            try:
                parse_episode(item.remote_path, package.remote_path)
            except ProviderError as error:
                if error.code == "multi_episode_file_unsupported":
                    raise HTTPException(409, error.code) from None
            if session.scalar(
                select(Media.id).where(
                    Media.package_id == package_id,
                    Media.id != media_id,
                    Media.season == body.season,
                    Media.episode == body.episode,
                )
            ):
                raise HTTPException(409, "duplicate_episode_mapping")
            item.season, item.episode, item.manual_mapping = body.season, body.episode, True
            item.mapping_status, item.error_code = "mapped", None
            invalidate_package(package, "episode_mapping_changed")
            record = session.get(MovieRecord, package.id)
            record.scan_status, record.error_code = "needs_scan", None
            if session.get(SeasonRecord, (package_id, body.season)) is None:
                session.add(SeasonRecord(package_id=package_id, number=body.season))
            session.add(
                Event(
                    code="episode_mapping_corrected",
                    details={
                        "package_id": package_id,
                        "media_id": media_id,
                        "season": body.season,
                        "episode": body.episode,
                    },
                )
            )
            version = package.context_version
        return {"accepted": True, "context_version": version}

    class BatchSubmit(BaseModel):
        package_ids: list[str] = Field(min_length=1, max_length=100)
        idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9:_-]+$")

    @app.post("/api/batch/tasks", dependencies=[Depends(mutation)])
    def batch_submit(body: BatchSubmit):
        results = []
        for index, package_id in enumerate(dict.fromkeys(body.package_ids)):
            with app.state.db.sessions.begin() as session:
                package = session.get(Package, package_id)
            try:
                if package is None:
                    raise HTTPException(404, "package_not_found")
                result = submit_task(
                    SubmitTask(
                        kind="package_pipeline",
                        package_path=package.remote_path,
                        idempotency_key=f"{body.idempotency_key}:{index}",
                    )
                )
                results.append({"id": package_id, "accepted": True, **result})
            except HTTPException as error:
                results.append({"id": package_id, "accepted": False, "error_code": error.detail})
        return {"items": results}

    class BatchControl(BaseModel):
        task_ids: list[str] = Field(min_length=1, max_length=100)
        action: Literal["pause", "resume", "retry"]

    @app.post("/api/batch/control", dependencies=[Depends(mutation)])
    def batch_control(body: BatchControl):
        results = []
        for task_id in dict.fromkeys(body.task_ids):
            try:
                app.state.queue.control(task_id, body.action)
                results.append({"id": task_id, "accepted": True})
            except (LookupError, ValueError) as error:
                results.append(
                    {
                        "id": task_id,
                        "accepted": False,
                        "error_code": "task_not_found"
                        if isinstance(error, LookupError)
                        else "task_action_not_allowed",
                    }
                )
        return {"items": results}

    @app.post("/api/assets/{asset_id}/retry", dependencies=[Depends(mutation)])
    def retry_asset(asset_id: str, body: ConnectionCheck):
        config, revision = app.state.store.load()
        if config is None:
            raise HTTPException(409, "configuration_required")
        with app.state.db.sessions.begin() as session:
            asset = session.get(Asset, asset_id)
            if not asset or asset.kind not in {
                "nfo",
                "poster",
                "fanart",
                "actor",
                "season_poster",
                "thumb",
            }:
                raise HTTPException(404, "asset_retry_not_allowed")
            if asset.kind == "actor" and not config.scrape_actor_images:
                raise HTTPException(409, "actor_images_disabled")
            package = session.get(Package, asset.package_id)
            if package.lease_owner and (package.lease_until or 0) > time.time():
                raise HTTPException(409, "package_busy_pause_first")
            if package.source_snapshot.get("config_revision") != revision:
                raise HTTPException(409, "configuration_changed_submit_new_task")
        try:
            task_id = app.state.queue.submit(
                "asset_retry",
                package.remote_path,
                body.idempotency_key,
                revision,
                checkpoint={"asset_id": asset_id},
            )
        except ValueError:
            raise HTTPException(409, "idempotency_key_conflict") from None
        return {"task_id": task_id}

    @app.get("/api/assets/{asset_id}/preview", dependencies=[Depends(authenticated)])
    def preview(asset_id: str):
        with app.state.db.sessions.begin() as session:
            asset = session.get(Asset, asset_id)
            if not asset or asset.kind not in {
                "nfo",
                "poster",
                "fanart",
                "actor",
                "season_poster",
                "thumb",
                "subtitle",
                "manifest",
                "subtitle_marker",
            }:
                raise HTTPException(404, "preview_not_available")
            body = app.state.worker.movies.cache.get(asset.cache_key)
            if body is None:
                raise HTTPException(404, "preview_cache_missing_retry")
            if asset.kind == "subtitle":
                if PurePosixPath(asset.relative_path).suffix.lower() not in {
                    ".srt",
                    ".ass",
                    ".ssa",
                }:
                    raise HTTPException(404, "preview_not_available")
                body = decode(body).encode("utf-8")
            return Response(
                body,
                media_type="text/plain; charset=utf-8"
                if asset.kind in {"nfo", "subtitle", "manifest", "subtitle_marker"}
                else "image/jpeg",
                headers={"Content-Security-Policy": "default-src 'none'; sandbox"},
            )

    def serialize_task(session, task):
        package = session.get(Package, task.package_id)
        steps = session.scalars(
            select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.id)
        )
        return {
            "id": task.id,
            "package_id": task.package_id,
            "kind": task.kind,
            "package_path": None if package.kind == "connection" else package.remote_path,
            "original_language": package.original_language,
            "base_status": package.base_status,
            "subtitle_status": package.subtitle_status,
            "subtitle_reason": package.subtitle_reason,
            "status": task.status,
            "stage": task.stage,
            "attempts": task.attempts,
            "retry_at": task.retry_at,
            "pause_requested": task.pause_requested,
            "error_code": task.error_code,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "steps": [
                {
                    "stage": step.stage,
                    "status": step.status,
                    "attempts": step.attempts,
                    "error_code": step.error_code,
                    "checkpoint": step.checkpoint,
                }
                for step in steps
            ],
        }

    @app.get("/api/tasks", dependencies=[Depends(authenticated)])
    def tasks(limit: int = Query(default=100, ge=1, le=200)):
        with app.state.db.sessions.begin() as session:
            return {
                "items": [
                    serialize_task(session, task)
                    for task in session.scalars(
                        select(Task).order_by(Task.created_at.desc(), Task.id).limit(limit)
                    )
                ]
            }

    @app.get("/api/tasks/{task_id}", dependencies=[Depends(authenticated)])
    def task_detail(task_id: str):
        with app.state.db.sessions.begin() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise HTTPException(404, "task_not_found")
            return serialize_task(session, task)

    @app.post("/api/tasks/{task_id}/{action}", dependencies=[Depends(mutation)])
    def control(task_id: str, action: Literal["pause", "resume", "retry"]):
        try:
            app.state.queue.control(task_id, action)
        except LookupError:
            raise HTTPException(404, "task_not_found") from None
        except ValueError:
            raise HTTPException(409, "task_action_not_allowed") from None
        return {"accepted": True}

    @app.get("/api/events/stream")
    async def event_stream(
        request: Request, after: int = Query(default=0, ge=0), auth=Depends(authenticated)
    ):
        raw = request.headers.get("last-event-id")
        if raw is not None:
            if not raw.isdecimal() or len(raw) > 18:
                raise HTTPException(422, "invalid_event_cursor")
            after = int(raw)
        return StreamingResponse(
            stream_events(app.state.db, auth.token_hash, after, request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/events", dependencies=[Depends(authenticated)])
    def events(after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)):
        with app.state.db.sessions.begin() as session:
            rows = list(
                session.scalars(
                    select(Event).where(Event.id > after).order_by(Event.id).limit(limit)
                )
            )
            return {
                "items": [
                    {
                        "id": row.id,
                        "task_id": row.task_id,
                        "code": row.code,
                        "details": row.details,
                        "created_at": row.created_at,
                    }
                    for row in rows
                ],
                "next_cursor": rows[-1].id if rows else after,
                "transport": "polling",
                "sse_available": True,
            }

    # Static frontend and API share one origin and one process in the development container.
    directory = runtime.frontend_dir.resolve()
    if (directory / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=directory / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        if (directory / "index.html").is_file():
            return FileResponse(directory / "index.html", headers={"Cache-Control": "no-cache"})
        return JSONResponse({"app": "ReelDock", "frontend": "run pnpm build in frontend"})

    return app


app = create_app()
