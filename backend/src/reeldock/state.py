"""Local state checks and single-instance ownership (Linux / macOS)."""

import fcntl
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet, InvalidToken


class StateError(RuntimeError):
    pass


@contextmanager
def state_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / ".instance.lock"
    if path.is_symlink():
        raise StateError("state_lock_invalid")
    with path.open("a+b") as stream:
        path.chmod(0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StateError("state_in_use_stop_application_first") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def revisions():
    scripts = ScriptDirectory(str(Path(__file__).parent / "migrations"))
    return {revision.revision for revision in scripts.walk_revisions()}


def head_revision():
    return ScriptDirectory(str(Path(__file__).parent / "migrations")).get_current_head()


def inspect_state(database: Path, key: Path) -> dict:
    """Read only; refuse unknown schemas or a broken key before any migration."""
    if database.is_symlink() or key.is_symlink():
        raise StateError("state_symlink_rejected")
    if not database.exists():
        return {"schema": None, "configured": False}
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            if source.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
                raise StateError("state_database_invalid")
            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master")}
            schema = None
            if "alembic_version" in tables:
                rows = source.execute("SELECT version_num FROM alembic_version").fetchall()
                if len(rows) != 1 or rows[0][0] not in revisions():
                    raise StateError("state_schema_not_supported_restore_matching_backup")
                schema = rows[0][0]
            elif tables:
                raise StateError("state_schema_not_supported_restore_matching_backup")
            config = (
                source.execute("SELECT encrypted FROM configuration WHERE id=1").fetchone()
                if "configuration" in tables
                else None
            )
            if config:
                if not key.is_file():
                    raise StateError("credential_key_missing_restore_master_key")
                try:
                    Fernet(key.read_bytes()).decrypt(config[0])
                except (InvalidToken, ValueError):
                    raise StateError("credential_key_mismatch_restore_master_key") from None
            elif key.exists():
                try:
                    Fernet(key.read_bytes())
                except ValueError:
                    raise StateError("credential_key_mismatch_restore_master_key") from None
            return {"schema": schema, "configured": bool(config)}
    except (sqlite3.Error, OSError):
        raise StateError("state_database_unreadable") from None
