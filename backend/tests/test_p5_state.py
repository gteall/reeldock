import asyncio
import io
import json
import signal
import sqlite3
import tarfile
from pathlib import Path

import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from reeldock.app import create_app
from reeldock.backup import backup, restore
from reeldock.database import Database
from reeldock.domain import ConfigUpdate
from reeldock.models import Task
from reeldock.queue import Queue
from reeldock.runtime import Runtime
from reeldock.security import SettingsStore, Vault
from reeldock.server import Server
from reeldock.state import StateError, inspect_state, state_lock
from reeldock.worker import Worker


@pytest.fixture
def foundation(tmp_path, config_body):
    db = Database(tmp_path / "reeldock.sqlite3")
    db.migrate()
    store = SettingsStore(db, Vault(tmp_path))
    store.save(ConfigUpdate(**config_body))
    yield db, store, Queue(db)
    db.close()


def read_db(path, query):
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute(query).fetchall()


def archive_entries(path):
    with tarfile.open(path) as archive:
        return {entry.name: archive.extractfile(entry).read() for entry in archive}


def write_archive(path, contents):
    with tarfile.open(path, "w:gz") as archive:
        for name, body in contents.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))


def test_online_backup_includes_wal_and_matching_key(foundation, tmp_path, config_body):
    db, store, queue = foundation
    task = queue.submit("movie_base", "/incoming/Generated", "backup-pending", 1)
    original_key = (tmp_path / "master.key").read_bytes()
    # A live DB and active service lock are valid for an online backup.
    with state_lock(tmp_path):
        report = backup(tmp_path, tmp_path / "snapshot.tar.gz")
    target = tmp_path / "restored"
    assert restore(tmp_path / "snapshot.tar.gz", target)["status"] == "passed"
    assert report["schema"] == "0005"
    assert (target / "master.key").read_bytes() == original_key
    assert (tmp_path / "snapshot.tar.gz").stat().st_mode & 0o777 == 0o600
    assert (target / "master.key").stat().st_mode & 0o777 == 0o600
    assert read_db(target / "reeldock.sqlite3", "SELECT id FROM tasks") == [(task,)]
    assert set(archive_entries(tmp_path / "snapshot.tar.gz")) == {
        "backup.json",
        "master.key",
        "reeldock.sqlite3",
    }
    restored = Database(target / "reeldock.sqlite3")
    try:
        restored.migrate()
        config, revision = SettingsStore(restored, Vault(target)).load()
        assert config.webdav_password.get_secret_value() == config_body["webdav_password"]
        assert config.tmdb_token.get_secret_value() == config_body["tmdb_token"]
        assert revision == 1
    finally:
        restored.close()


@pytest.mark.parametrize(
    "damage", ["checksum", "key", "traversal", "duplicate", "symlink", "unknown_schema"]
)
def test_restore_rejects_bad_archive_without_partial_state(foundation, tmp_path, damage):
    source = tmp_path / "good.tar.gz"
    backup(tmp_path, source)
    entries = archive_entries(source)
    if damage == "checksum":
        entries["master.key"] = Fernet.generate_key()
    if damage == "key":
        import hashlib

        entries["master.key"] = Fernet.generate_key()
        manifest = json.loads(entries["backup.json"])
        manifest["files"]["master.key"]["sha256"] = hashlib.sha256(
            entries["master.key"]
        ).hexdigest()
        entries["backup.json"] = json.dumps(manifest).encode()
    if damage == "traversal":
        entries["../outside"] = b"forbidden"
    if damage == "unknown_schema":
        import hashlib

        snapshot = tmp_path / "future.sqlite3"
        snapshot.write_bytes(entries["reeldock.sqlite3"])
        with sqlite3.connect(snapshot) as connection:
            connection.execute("UPDATE alembic_version SET version_num='future'")
        entries["reeldock.sqlite3"] = snapshot.read_bytes()
        manifest = json.loads(entries["backup.json"])
        manifest["files"]["reeldock.sqlite3"]["sha256"] = hashlib.sha256(
            entries["reeldock.sqlite3"]
        ).hexdigest()
        entries["backup.json"] = json.dumps(manifest).encode()
    damaged = tmp_path / "bad.tar.gz"
    write_archive(damaged, entries)
    if damage in {"duplicate", "symlink"}:
        with tarfile.open(damaged, "w:gz") as archive:
            for _ in range(2 if damage == "duplicate" else 1):
                entry = tarfile.TarInfo("backup.json")
                if damage == "symlink":
                    entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/passwd"
                archive.addfile(entry)
    target = tmp_path / "restore-target"
    with pytest.raises(StateError):
        restore(damaged, target)
    assert not (target / "reeldock.sqlite3").exists()
    assert not (target / "master.key").exists()
    assert not (tmp_path / "outside").exists()


def test_backup_never_overwrites_and_restore_requires_empty_inactive_target(foundation, tmp_path):
    output = tmp_path / "snapshot.tar.gz"
    backup(tmp_path, output)
    original = output.read_bytes()
    with pytest.raises(StateError, match="backup_output_exists"):
        backup(tmp_path, output)
    assert output.read_bytes() == original
    with pytest.raises(StateError, match="restore_target_not_empty"):
        restore(output, tmp_path)
    target = tmp_path / "empty"
    with state_lock(target):
        with pytest.raises(StateError, match="state_in_use"):
            restore(output, target)


def test_second_app_instance_is_rejected(client, runtime):
    with pytest.raises(StateError, match="state_in_use"):
        with TestClient(create_app(runtime)):
            pass
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("damage", ["missing_key", "wrong_key", "future_schema"])
def test_preflight_rejects_state_before_migration(foundation, tmp_path, monkeypatch, damage):
    db, _, _ = foundation
    if damage == "missing_key":
        (tmp_path / "master.key").unlink()
    if damage == "wrong_key":
        (tmp_path / "master.key").write_bytes(Fernet.generate_key())
    if damage == "future_schema":
        with db.engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='future'"))

    def forbidden(self):
        pytest.fail("Invalid credentials/schema must not start migration")

    monkeypatch.setattr(Database, "migrate", forbidden)
    with pytest.raises(StateError):
        with TestClient(create_app(Runtime(data_dir=tmp_path, worker_enabled=False))):
            pass


def test_upgrade_and_restore_original_schema_on_failed_upgrade(foundation, tmp_path):
    db, _, queue = foundation
    task_id = queue.submit("movie_base", "/incoming/Generated", "upgrade-pending", 1)
    config = Config()
    import reeldock.database

    config.set_main_option(
        "script_location", str(Path(reeldock.database.__file__).parent / "migrations")
    )
    with db.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0003")
    snapshot = tmp_path / "before-upgrade.tar.gz"
    backup(tmp_path, snapshot)
    db.migrate()
    assert inspect_state(tmp_path / "reeldock.sqlite3", tmp_path / "master.key")["schema"] == "0005"
    with db.engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='broken-upgrade'"))
    target = tmp_path / "rollback"
    restore(snapshot, target)
    assert inspect_state(target / "reeldock.sqlite3", target / "master.key")["schema"] == "0003"
    rollback_db = Database(target / "reeldock.sqlite3")
    try:
        rollback_db.migrate()
        with rollback_db.sessions.begin() as session:
            assert session.get(Task, task_id).status == "queued"
    finally:
        rollback_db.close()


def test_separate_cache_is_rebuildable_and_old_layout_is_default(foundation, tmp_path):
    db, store, queue = foundation
    runtime = Runtime(data_dir=tmp_path, cache_dir=tmp_path / "separate-cache")
    worker = Worker(queue, store, runtime)
    key = worker.movies.cache.put(b"generated asset")
    assert worker.movies.cache.get(key) == b"generated asset"
    assert (runtime.asset_cache_path / key).exists()
    assert Runtime(data_dir=tmp_path).asset_cache_path == tmp_path / "cache" / "assets"
    (runtime.asset_cache_path / key).unlink()
    assert worker.movies.cache.get(key) is None


async def test_signal_drains_new_claims_and_interrupts_current_safely(foundation, tmp_path):
    db, store, queue = foundation
    first = queue.submit("connection_check", "/incoming", "drain-first", 1)
    second = queue.submit("connection_check", "/incoming", "drain-second", 1)
    started = asyncio.Event()

    async def handler(claim):
        started.set()
        await asyncio.Event().wait()

    worker = Worker(
        queue,
        store,
        Runtime(data_dir=tmp_path, worker_concurrency=1),
        handlers={"connection_check": handler},
    )
    app = create_app(Runtime(data_dir=tmp_path, worker_enabled=False))
    app.state.worker = worker
    server = Server(uvicorn.Config(app))
    await worker.start()
    await asyncio.wait_for(started.wait(), 2)
    server.handle_exit(signal.SIGTERM, None)
    assert worker.draining and server.should_exit
    await worker.stop()
    with db.sessions.begin() as session:
        assert session.get(Task, first).status == "queued"
        assert session.get(Task, second).attempts == 0
