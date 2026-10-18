"""Isolated P3 UI fixture. In-memory providers only; no NAS config is imported."""

import argparse
import json
import secrets
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "tests"))
from p2_support import FakeTMDB  # noqa: E402
from p3_support import DAV, Probe, Shooter  # noqa: E402

from reeldock.app import create_app  # noqa: E402
from reeldock.database import Database  # noqa: E402
from reeldock.domain import ConfigUpdate  # noqa: E402
from reeldock.runtime import Runtime  # noqa: E402
from reeldock.security import SettingsStore, Vault, init_admin  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8023)
    parser.add_argument("--data-dir", type=Path, required=True, help="Must be a fresh directory")
    args = parser.parse_args()
    if args.data_dir.exists():
        parser.error("Use a fresh directory: the fake remote storage does not persist")
    args.data_dir.mkdir(mode=0o700, parents=True)
    runtime = Runtime(data_dir=args.data_dir, allowed_origins=[f"http://127.0.0.1:{args.port}"])
    db = Database(runtime.database_path)
    db.migrate()
    password = secrets.token_urlsafe(24)
    init_admin(db, "admin", password)
    target = args.data_dir / "ui-login.json"
    target.write_text(json.dumps({"username": "admin", "password": password}))
    target.chmod(0o600)
    SettingsStore(db, Vault(args.data_dir)).save(
        ConfigUpdate(
            webdav_url="https://dav.example/dav/",
            input_path="/incoming",
            output_path="/library",
            tmdb_token="mock-only-token",
            stable_seconds=1,
            move_verified=True,
        )
    )
    db.close()
    dav = DAV()
    metadata = FakeTMDB()
    # Two independent packages exercise both skip and actual default-audio evidence.
    dav.directories.add("/incoming/Chinese (2020)")
    dav.media["/incoming/Chinese (2020)/Chinese.2020.mkv"] = 131072
    metadata.movies.append(
        metadata.movies[0].model_copy(
            update={
                "external_id": "124",
                "title": "Chinese",
                "original_title": "Chinese",
                "original_language": "zh",
            }
        )
    )
    app = create_app(
        runtime,
        storage_factory=dav,
        metadata_factory=metadata,
        probe=Probe(dav),
        subtitle_factory=Shooter(dav),
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
