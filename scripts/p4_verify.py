"""P4 verification: Mock matrix, read-only TMDB, isolated synthetic WebDAV TV package."""

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

from reeldock.cache import Cache
from reeldock.database import Database
from reeldock.domain import AppConfig, ProviderError
from reeldock.providers.tmdb import TMDBProvider


async def tmdb(args):
    values = dotenv_values(args.tmdb_env, interpolate=False)
    if not values.get("P2_TMDB_TOKEN"):
        return {"status": "unverified", "code": "missing_tmdb_credentials"}
    folder = Path("reports/p4-tmdb-cache")
    folder.mkdir(parents=True, exist_ok=True)
    db = Database(folder / "test.sqlite3")
    db.migrate()
    try:
        config = AppConfig(
            webdav_url="https://dav.example/dav/",
            scrape_paths=["/input"],
            tmdb_token=values["P2_TMDB_TOKEN"],
            tmdb_proxy_url=values.get("P2_PROXY_URL"),
            http_timeout_seconds=30,
        )
        async with TMDBProvider(config, Cache(db, folder / "cache")) as metadata:
            series = await metadata.series_details(args.series_id)
            season = await metadata.season_details(args.series_id, 1)
            episode = await metadata.episode_details(args.series_id, 1, 1)
            images = await metadata.images(args.series_id, "tv")
            images += await metadata.images(args.series_id, "tv", season=1)
            images += await metadata.images(args.series_id, "tv", season=1, episode=1)
            verified = []
            for kind in ["poster", "fanart", "season_poster", "thumb"]:
                art = next((a for a in images if a.kind == kind), None)
                if art:
                    body = await metadata.download_artwork(art)
                    verified.append({"kind": kind, "bytes": len(body)})
            if episode.original_language != series.original_language:
                raise ProviderError("episode_language_inheritance_failed")
            return {
                "status": "passed",
                "scope": "real_tmdb_read_only_no_video",
                "series_id": series.external_id,
                "original_language": series.original_language,
                "season_episodes": len(season.episodes),
                "episode_language": episode.original_language,
                "images": verified,
                "webdav_pipeline": "unverified",
                "kodi": "unverified",
            }
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["mock", "tmdb", "webdav"])
    parser.add_argument("--tmdb-env", default=".env.p2")
    parser.add_argument("--webdav-env", default=".env.p0")
    parser.add_argument("--series-id", default="1399")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "mock":
            files = sorted(str(p) for p in Path("backend/tests").glob("test_p4_*.py"))
            completed = subprocess.run([sys.executable, "-m", "pytest", "-q", *files], check=False)
            result = {
                "status": "passed" if completed.returncode == 0 else "failed",
                "scope": "mock_p4_workflow_api_protocol_sse",
            }
        elif args.mode == "tmdb":
            result = asyncio.run(tmdb(args))
        else:
            from p3_verify import webdav

            result = asyncio.run(webdav(args, tv=True))
    except ProviderError as error:
        result = {"status": "failed", "scope": args.mode, "code": error.code}
    except Exception:
        result = {"status": "failed", "scope": args.mode, "code": "verification_internal_error"}
    body = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body + "\n")
    print(body)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
