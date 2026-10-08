import asyncio
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select, text

from reeldock import __version__
from reeldock.database import Database
from reeldock.domain import ConfigUpdate, normalize_path
from reeldock.models import (
    Admin,
    Asset,
    AuthSession,
    Configuration,
    Event,
    LoginLimit,
    MovieRecord,
    Package,
    Task,
    TaskStep,
)
from reeldock.queue import Queue, revise_package
from reeldock.runtime import Runtime
from reeldock.security import (
    SettingsStore,
    Vault,
    configure_logging,
    digest,
    passwords,
    random_token,
)
from reeldock.worker import Worker

COOKIE = "reeldock_session"


class Login(BaseModel):
    username: str = Field(max_length=128)
    password: SecretStr = Field(max_length=1024)


class SubmitTask(BaseModel):
    package_path: str = Field(max_length=2048)
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9:_-]+$")


class ConnectionCheck(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9:_-]+$")


def create_app(
    runtime: Runtime | None = None, *, storage_factory=None, metadata_factory=None
) -> FastAPI:
    runtime = runtime or Runtime()

    @asynccontextmanager
    async def lifespan(app):
        configure_logging()
        os.umask(0o077)
        runtime.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = Database(runtime.database_path)
        db.migrate()
        with db.sessions.begin() as session:
            if session.get(Configuration, 1) and not (runtime.data_dir / "master.key").is_file():
                db.close()
                raise RuntimeError("credential_key_missing_restore_master_key")
        vault = Vault(runtime.data_dir)
        store = SettingsStore(db, vault)
        store.load()  # Fail closed if an existing database cannot be decrypted.
        queue = Queue(db, lease_seconds=runtime.lease_seconds)
        worker = Worker(
            queue,
            store,
            runtime,
            **({"storage_factory": storage_factory} if storage_factory else {}),
            **({"metadata_factory": metadata_factory} if metadata_factory else {}),
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

    @app.get("/api/health")
    def health():
        with app.state.db.sessions.begin() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__, "phase": "P2", "archive_enabled": False}

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
        if not path.startswith(config.input_path + "/"):
            raise HTTPException(422, "package_must_be_below_input_directory")
        try:
            task_id = app.state.queue.submit("movie_base", path, body.idempotency_key, revision)
        except ValueError:
            raise HTTPException(409, "idempotency_key_conflict") from None
        return {"task_id": task_id, "implementation": "movie_base_only"}

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

    def movie_view(session, row):
        package = session.get(Package, row.package_id)
        assets = list(
            session.scalars(
                select(Asset).where(Asset.package_id == package.id).order_by(Asset.relative_path)
            )
        )
        latest = session.scalar(
            select(Task)
            .where(Task.package_id == package.id)
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        return {
            "id": package.id,
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
            "original_language": package.original_language,
            "candidates": row.candidates,
            "base_status": package.base_status,
            "subtitle_status": "not_entered_p2",
            "probe_status": "not_started",
            "archive_enabled": False,
            "error_code": row.error_code or (latest.error_code if latest else None),
            "task_id": latest.id if latest else None,
            "task_status": latest.status if latest else None,
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
                    "path": a.relative_path,
                    "kind": a.kind,
                    "required": a.required,
                    "status": a.status,
                    "error_code": a.error_code,
                    "managed": a.managed,
                    "sha256": a.sha256,
                    "remote_verified_at": a.remote_verified_at,
                    "preview": "/api/assets/" + a.id + "/preview" if a.cache_key else None,
                }
                for a in assets
            ],
        }

    @app.get("/api/movies", dependencies=[Depends(authenticated)])
    def movies():
        with app.state.db.sessions.begin() as session:
            return {
                "items": [
                    movie_view(session, row)
                    for row in session.scalars(select(MovieRecord).order_by(MovieRecord.title))
                ]
            }

    @app.get("/api/movies/{package_id}", dependencies=[Depends(authenticated)])
    def movie_detail(package_id: str):
        with app.state.db.sessions.begin() as session:
            row = session.get(MovieRecord, package_id)
            if not row:
                raise HTTPException(404, "movie_not_found")
            return movie_view(session, row)

    class ManualMatch(BaseModel):
        tmdb_id: str = Field(pattern=r"^[1-9]\d{0,9}$")

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
            session.add(Event(code="manual_tmdb_id_selected", details={"package_id": package_id}))
        return {"accepted": True}

    @app.get("/api/assets/{asset_id}/preview", dependencies=[Depends(authenticated)])
    def preview(asset_id: str):
        with app.state.db.sessions.begin() as session:
            asset = session.get(Asset, asset_id)
            if not asset or asset.kind not in {"nfo", "poster", "fanart", "actor"}:
                raise HTTPException(404, "preview_not_available")
            body = app.state.worker.movies.cache.get(asset.cache_key)
            if body is None:
                raise HTTPException(404, "preview_cache_missing_retry")
            return Response(
                body,
                media_type="text/plain; charset=utf-8" if asset.kind == "nfo" else "image/jpeg",
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
                "sse_planned": True,
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
