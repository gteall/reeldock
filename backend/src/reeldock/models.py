import time
import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Configuration(Base):
    __tablename__ = "configuration"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    revision: Mapped[int] = mapped_column(default=1)
    encrypted: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[float] = mapped_column(default=time.time)


class Admin(Base):
    __tablename__ = "admins"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class LoginLimit(Base):
    __tablename__ = "login_limits"
    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(default=0)
    reset_at: Mapped[float] = mapped_column(Float)


class Package(Base):
    __tablename__ = "packages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    remote_path: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(String(32), default="movie")
    tmdb_id: Mapped[str | None] = mapped_column(String(32))
    original_language: Mapped[str | None] = mapped_column(String(32))
    source_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    context_version: Mapped[int] = mapped_column(default=1)
    policy_version: Mapped[int] = mapped_column(default=1)
    base_required: Mapped[list] = mapped_column(JSON, default=list)
    base_status: Mapped[str] = mapped_column(String(40), default="pending")
    subtitle_status: Mapped[str] = mapped_column(String(40), default="pending")
    subtitle_reason: Mapped[str | None] = mapped_column(String(80))
    subtitle_version: Mapped[int | None] = mapped_column(Integer)
    manifest_version: Mapped[int | None] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[float | None] = mapped_column(Float)
    lease_generation: Mapped[int] = mapped_column(default=0)


class Media(Base):
    __tablename__ = "media"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), index=True)
    remote_path: Mapped[str] = mapped_column(Text, unique=True)
    size: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(Text)
    season: Mapped[int | None] = mapped_column(Integer)
    episode: Mapped[int | None] = mapped_column(Integer)
    probe_status: Mapped[str] = mapped_column(String(32), default="not_started")
    probe_evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (UniqueConstraint("package_id", "relative_path"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), index=True)
    relative_path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32))
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    sha256: Mapped[str | None] = mapped_column(String(64))
    remote_verified_at: Mapped[float | None] = mapped_column(Float)
    verified_version: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    cache_key: Mapped[str | None] = mapped_column(String(64))
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    source_tmdb_id: Mapped[str | None] = mapped_column(String(32))


class MovieRecord(Base):
    __tablename__ = "movie_records"
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer)
    media_path: Mapped[str | None] = mapped_column(Text)
    stable_since: Mapped[float] = mapped_column(Float, default=time.time)
    last_seen: Mapped[float] = mapped_column(Float, default=time.time)
    scan_status: Mapped[str] = mapped_column(String(40), default="waiting_stable")
    match_status: Mapped[str] = mapped_column(String(40), default="pending")
    manual_id: Mapped[str | None] = mapped_column(String(32))
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    cast: Mapped[list] = mapped_column(JSON, default=list)
    artwork: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(80))


class ProviderCache(Base):
    __tablename__ = "provider_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    body: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[float] = mapped_column(Float)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    context_version: Mapped[int] = mapped_column(Integer)
    config_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=3)
    retry_at: Mapped[float] = mapped_column(Float, default=0, index=True)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(32))
    lease_generation: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[float] = mapped_column(default=time.time)
    updated_at: Mapped[float] = mapped_column(default=time.time)


class TaskStep(Base):
    __tablename__ = "task_steps"
    __table_args__ = (UniqueConstraint("task_id", "stage"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[float] = mapped_column(default=time.time)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    code: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(default=time.time)


class ArchiveIntent(Base):
    __tablename__ = "archive_intents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    package_id: Mapped[str] = mapped_column(ForeignKey("packages.id"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    source: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[dict] = mapped_column(JSON)
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="planned")
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[float] = mapped_column(default=time.time)
    updated_at: Mapped[float] = mapped_column(default=time.time)
