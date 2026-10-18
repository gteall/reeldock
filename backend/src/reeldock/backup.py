"""SQLite online backup plus its credential key; restore only into empty local state."""

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from contextlib import closing
from pathlib import Path

from reeldock import __version__
from reeldock.state import StateError, inspect_state, state_lock

DATABASE = "reeldock.sqlite3"
KEY = "master.key"
MANIFEST = "backup.json"
MAX_DATABASE = 4 * 1024**3


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def backup(directory: Path, output: Path):
    source_path, key = directory / DATABASE, directory / KEY
    if not source_path.is_file():
        raise StateError("backup_database_missing")
    if source_path.is_symlink() or key.is_symlink():
        raise StateError("state_symlink_rejected")
    if output.exists() or output.is_symlink():
        raise StateError("backup_output_exists")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + 60

    def progress(*_):
        if time.monotonic() > deadline:
            raise StateError("backup_timeout")

    # No service lock: sqlite3.backup includes committed WAL pages while the app runs.
    with tempfile.TemporaryDirectory(prefix=".reeldock-backup-", dir=output.parent) as work:
        work = Path(work)
        snapshot = work / DATABASE
        with closing(
            sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as source:
            with closing(sqlite3.connect(snapshot)) as destination:
                source.backup(destination, pages=256, progress=progress, sleep=0.1)
                destination.execute("PRAGMA journal_mode=DELETE")
        if snapshot.stat().st_size > MAX_DATABASE:
            raise StateError("backup_database_too_large")
        if key.is_file() and not key.is_symlink():
            shutil.copyfile(key, work / KEY)
        checked = inspect_state(snapshot, work / KEY)
        files = [DATABASE] + ([KEY] if (work / KEY).is_file() else [])
        metadata = {
            "format": 1,
            "application_version": __version__,
            "created_at": time.time(),
            **checked,
            "files": {
                name: {"sha256": file_hash(work / name), "size": (work / name).stat().st_size}
                for name in files
            },
            "cache_included": False,
        }
        (work / MANIFEST).write_text(json.dumps(metadata, indent=2) + "\n")
        temporary = work / "archive.tar.gz"
        with tarfile.open(temporary, "w:gz") as archive:
            for name in [MANIFEST, *files]:
                (work / name).chmod(0o600)
                archive.add(work / name, arcname=name, recursive=False)
        temporary.chmod(0o600)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        # Publish atomically without ever overwriting an existing backup.
        try:
            os.link(temporary, output)
        except FileExistsError:
            raise StateError("backup_output_exists") from None
    return {"status": "passed", "schema": checked["schema"], "cache_included": False}


def unpack(archive_path: Path, work: Path):
    with tarfile.open(archive_path, "r|gz") as archive:
        names = []
        for member in archive:
            if (
                member.name not in {MANIFEST, DATABASE, KEY}
                or member.name in names
                or not member.isfile()
            ):
                raise StateError("backup_archive_invalid")
            names.append(member.name)
            limit = MAX_DATABASE if member.name == DATABASE else 65536
            if member.size < 0 or member.size > limit:
                raise StateError("backup_archive_too_large")
            source = archive.extractfile(member)
            if source is None:
                raise StateError("backup_archive_invalid")
            # Fixed basenames only; never extract symlinks, permissions or tar paths.
            target = work / member.name
            with source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            target.chmod(0o600)
        if not {MANIFEST, DATABASE} <= set(names):
            raise StateError("backup_archive_invalid")
        metadata = json.loads((work / MANIFEST).read_bytes())
        if not isinstance(metadata, dict):
            raise StateError("backup_manifest_invalid")
        expected = set(names) - {MANIFEST}
        if metadata.get("format") != 1 or set(metadata.get("files", {})) != expected:
            raise StateError("backup_manifest_invalid")
        for name in expected:
            details = metadata["files"][name]
            if details["size"] != (work / name).stat().st_size or details["sha256"] != file_hash(
                work / name
            ):
                raise StateError("backup_checksum_mismatch")
        checked = inspect_state(work / DATABASE, work / KEY)
        if metadata.get("schema") != checked["schema"]:
            raise StateError("backup_schema_mismatch")
        return checked


def restore(archive: Path, directory: Path):
    # Stop the application first. Empty destination also enables side-by-side rollback.
    with state_lock(directory):
        if any(path.name != ".instance.lock" for path in directory.iterdir()):
            raise StateError("restore_target_not_empty")
        with tempfile.TemporaryDirectory(prefix=".reeldock-restore-", dir=directory) as work:
            work = Path(work)
            try:
                checked = unpack(archive, work)
            except (tarfile.TarError, KeyError, TypeError, ValueError, UnicodeError, EOFError):
                raise StateError("backup_archive_invalid") from None
            published = []
            try:
                for name in [KEY, DATABASE]:
                    if (work / name).exists():
                        os.link(work / name, directory / name)
                        published.append(directory / name)
            except OSError:
                for path in published:
                    path.unlink()
                raise
    return {"status": "passed", "schema": checked["schema"], "cache_included": False}
