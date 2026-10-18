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
from sqlalchemy import select

from reeldock.domain import AppConfig, ConfigUpdate
from reeldock.models import Admin, Configuration, Event

passwords = PasswordHash.recommended()


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
        for name in ("webdav_password", "tmdb_token"):
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
                }
            )
            for name in ("webdav_password", "tmdb_token"):
                if getattr(update, "clear_" + name):
                    value[name] = None
                elif not getattr(update, name) or not getattr(update, name).get_secret_value():
                    value[name] = getattr(previous, name) if previous else None
            config = AppConfig.model_validate(value)
            if previous and any(
                getattr(config, key) != getattr(previous, key)
                for key in (
                    "webdav_url",
                    "webdav_username",
                    "webdav_password",
                    "input_path",
                    "output_path",
                )
            ):
                config.move_verified = False
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
        result = config.model_dump(mode="json", exclude={"webdav_password", "tmdb_token"})
        result.update(
            configured=True,
            revision=revision,
            webdav_password_set=bool(config.webdav_password),
            tmdb_token_set=bool(config.tmdb_token),
        )
        return result


def init_admin(db, username: str, password: str):
    if not username.strip() or len(username) > 128 or len(password) < 12:
        raise ValueError("用户名不能为空，密码至少 12 位")
    hashed = passwords.hash(password)
    with db.sessions.begin() as session:
        if session.scalar(select(Admin)):
            raise ValueError("管理员已存在，不能重复初始化")
        session.add(Admin(id=1, username=username.strip(), password_hash=hashed))


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
