"""Local UI fixture, shared real application handlers + in-memory providers. No NAS."""

import argparse
import json
import secrets
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "tests"))
from p3_support import DAV, SRT  # noqa: E402
from p4_support import TVMetadata, TVProbe, TVShooter  # noqa: E402

from reeldock.app import create_app  # noqa: E402
from reeldock.database import Database  # noqa: E402
from reeldock.domain import ConfigUpdate  # noqa: E402
from reeldock.runtime import Runtime  # noqa: E402
from reeldock.security import SettingsStore, Vault, init_admin  # noqa: E402


class DemoMetadata(TVMetadata):
    def __init__(self):
        super().__init__("en")
        self.chinese = self.series.model_copy(
            update={
                "external_id": "124",
                "title": "Chinese",
                "original_title": "Chinese",
                "original_language": "zh",
            }
        )
        self.movies[0] = self.movies[0].model_copy(
            update={
                "external_id": "125",
                "title": "Cinema",
                "original_title": "Cinema",
                "original_language": "zh",
            }
        )

    async def search(self, query, kind, year=None):
        if kind == "movie":
            return self.movies
        return [self.chinese if query == "Chinese" else self.series]

    async def series_details(self, identifier):
        return self.chinese if identifier == "124" else self.series


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8024)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_dir.exists():
        parser.error("Use a fresh directory; in-memory remote fixtures do not persist")
    args.data_dir.mkdir(parents=True, mode=0o700)
    runtime = Runtime(
        data_dir=args.data_dir,
        worker_concurrency=1,
        allowed_origins=[f"http://127.0.0.1:{args.port}"],
    )
    db = Database(runtime.database_path)
    db.migrate()
    password = secrets.token_urlsafe(24)
    init_admin(db, "admin", password)
    login = args.data_dir / "ui-login.json"
    login.write_text(json.dumps({"username": "admin", "password": password}))
    login.chmod(0o600)
    SettingsStore(db, Vault(args.data_dir)).save(
        ConfigUpdate(
            webdav_url="https://dav.example/dav/",
            input_path="/incoming",
            output_path="/library",
            tmdb_token="mock-only",
            stable_seconds=1,
            move_verified=True,
        )
    )
    db.close()

    class DemoDAV(DAV):
        async def list(self, path):
            if (args.data_dir / "repair-subtitles").exists():
                self.files["/incoming/Example (2020)/Season 01/Example.S01E02.en.srt"] = SRT
            return await super().list(path)

    dav = DemoDAV()
    dav.directories = {"/incoming", "/library"}
    dav.media = {}
    for title in ["Chinese", "Example", "Cinema"]:
        root = f"/incoming/{title} (2020)"
        dav.directories.add(root)
        names = (
            [f"{title}.2020.mkv"]
            if title == "Cinema"
            else [f"Season 01/{title}.S01E01.mkv", f"Season 01/{title}.S01E02.mkv"]
        )
        for name in names:
            path = root + "/" + name
            dav.media[path] = 1024**3
            dav.directories.add(str(Path(path).parent))
    probe, shooter = TVProbe(dav), TVShooter(dav)
    shooter.missing.add(2)
    app = create_app(
        runtime,
        storage_factory=dav,
        metadata_factory=DemoMetadata(),
        probe=probe,
        subtitle_factory=shooter,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
