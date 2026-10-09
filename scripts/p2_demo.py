"""Local UI fixture, using only in-memory fake providers. Never uses .env.p0/.env.p2.

uv run python scripts/p2_demo.py --port 8022 --data-dir /tmp/reeldock-p2-demo
One random admin password is written to <data-dir>/ui-login.json (not stdout).
"""

import argparse
import json
import secrets
import sys
from pathlib import Path

import uvicorn

# Development fixtures are shared with the integration tests, never imported by production.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "tests"))
from p2_support import FakeTMDB, MemoryDAV  # noqa: E402

from reeldock.app import create_app  # noqa: E402
from reeldock.database import Database  # noqa: E402
from reeldock.domain import ConfigUpdate  # noqa: E402
from reeldock.models import Admin  # noqa: E402
from reeldock.runtime import Runtime  # noqa: E402
from reeldock.security import SettingsStore, Vault, init_admin  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8022)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    runtime = Runtime(data_dir=args.data_dir, allowed_origins=[f"http://127.0.0.1:{args.port}"])
    db = Database(runtime.database_path)
    db.migrate()
    with db.sessions.begin() as session:
        existing = session.get(Admin, 1) is not None
    if not existing:
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
            )
        )
    db.close()
    app = create_app(runtime, storage_factory=MemoryDAV(), metadata_factory=FakeTMDB())
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
