import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from pwdlib import PasswordHash
from sqlalchemy import delete, select, update

from reeldock.domain import AppConfig, ConfigUpdate
from reeldock.models import Admin, AuthSession, Configuration, Event, LoginLimit

passwords = PasswordHash.recommended()
CONFIG_SECRETS = ("webdav_password", "tmdb_token", "assrt_token")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Vault:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "master.key"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as file:
                file.write(Fernet.generate_key())
        path.chmod(0o600)
        self.cipher = Fernet(path.read_bytes())

    def seal(self, config: AppConfig) -> str:
        value = config.model_dump(mode="json")
        for name in CONFIG_SECRETS:
            secret = getattr(config, name)
            value[name] = secret.get_secret_value() if secret else None
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def open(self, value: str) -> AppConfig:
        try:
            return AppConfig.model_validate_json(self.cipher.decrypt(value))
        except InvalidToken:
            raise RuntimeError("credential_key_mismatch_restore_master_key") from None


class SettingsStore:
    def __init__(self, db, vault: Vault):
        self.db, self.vault = db, vault

    def load(self) -> tuple[AppConfig | None, int]:
        with self.db.sessions.begin() as session:
            row = session.get(Configuration, 1)
            return (self.vault.open(row.encrypted), row.revision) if row else (None, 0)

    def save(self, update: ConfigUpdate) -> int:
        with self.db.sessions.begin() as session:
            row = session.get(Configuration, 1)
            revision = row.revision if row else 0
            if update.expected_revision != revision:
                raise ValueError("configuration_revision_conflict")
            previous = self.vault.open(row.encrypted) if row else None
            value = update.model_dump(
                exclude={
                    "expected_revision",
                    "clear_webdav_password",
                    "clear_tmdb_token",
                    "clear_assrt_token",
                }
            )
            for name in CONFIG_SECRETS:
                if getattr(update, "clear_" + name):
                    value[name] = None
                elif not getattr(update, name) or not getattr(update, name).get_secret_value():
                    value[name] = getattr(previous, name) if previous else None
            config = AppConfig.model_validate(value)
            encrypted = self.vault.seal(config)
            if row:
                row.revision += 1
                row.encrypted, row.updated_at = encrypted, time.time()
            else:
                row = Configuration(id=1, encrypted=encrypted, revision=1)
                session.add(row)
            session.add(Event(code="configuration_saved", details={"revision": revision + 1}))
            return revision + 1

    def public(self) -> dict:
        config, revision = self.load()
        if config is None:
            return {"configured": False, "revision": revision}
        result = config.model_dump(mode="json", exclude=set(CONFIG_SECRETS))
        result.update(
            configured=True,
            revision=revision,
            webdav_password_set=bool(config.webdav_password),
            tmdb_token_set=bool(config.tmdb_token),
            assrt_token_set=bool(config.assrt_token),
        )
        return result


def init_admin(db, username: str, password: str):
    if not username.strip() or len(username) > 128 or len(password) < 8:
        raise ValueError("用户名不能为空，密码至少 8 位")
    hashed = passwords.hash(password)
    with db.sessions.begin() as session:
        if session.scalar(select(Admin)):
            raise ValueError("管理员已存在，不能重复初始化；请使用 reset-password 重置密码")
        session.add(Admin(id=1, username=username.strip(), password_hash=hashed))


def reset_admin_password(db, password: str):
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    hashed = passwords.hash(password)
    with db.sessions.begin() as session:
        admin = session.get(Admin, 1)
        if not admin:
            raise ValueError("管理员尚未初始化，请先运行 init-admin")
        admin.password_hash = hashed
        session.execute(delete(AuthSession))
        session.execute(update(LoginLimit).values(attempts=0))
        session.add(Event(code="administrator_password_reset", details={}))


class SafeFormatter(logging.Formatter):
    def format(self, record):
        # Whitelist event codes rather than trying to recognize every possible secret.
        code = getattr(record, "safe_code", "internal_log_redacted")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code):
            code = "internal_log_redacted"
        return json.dumps({"level": record.levelname, "code": code}, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        if name == "uvicorn.access":
            logger.disabled = True
        elif name in {"httpx", "httpcore"}:
            logger.setLevel(logging.WARNING)


def random_token() -> str:
    return secrets.token_urlsafe(32)
