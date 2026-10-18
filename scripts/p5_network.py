"""Run inside the NAS production image; credentials from stdin, safe summaries only.

Real service parts, never a claim of complete media workflow or subtitle matching.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from reeldock.cache import Cache
from reeldock.database import Database
from reeldock.domain import AppConfig, ProviderError
from reeldock.exporter import validate_image
from reeldock.providers.assrt import AssrtProvider
from reeldock.providers.tmdb import TMDBProvider
from reeldock.subtitles import validate_subtitle


def error_summary(error):
    return {
        "status": "unverified",
        "code": error.code if isinstance(error, ProviderError) else type(error).__name__,
    }


async def tmdb(values):
    if not values.get("token"):
        return {"status": "unverified", "code": "tmdb_token_missing"}
    try:
        with tempfile.TemporaryDirectory(prefix="p5-network-") as folder:
            db = Database(Path(folder) / "network.sqlite3")
            try:
                db.migrate()
                config = AppConfig(
                    webdav_url="https://dav.example/dav/",
                    scrape_paths=["/input"],
                    tmdb_token=values["token"],
                    tmdb_proxy_url=values.get("tmdb_proxy_url"),
                    http_timeout_seconds=12,
                )
                async with TMDBProvider(config, Cache(db, Path(folder) / "assets")) as provider:
                    movie = await provider.movie_details("550")
                    images = await provider.images("550")
                    art = next(item for item in images if item.kind == "poster")
                    body = await provider.download_artwork(art)
                    validate_image(body)
                return {
                    "status": "passed",
                    "scope": "nas_container_real_tmdb_details_and_image",
                    "original_language": movie.original_language,
                    "image_bytes": len(body),
                }
            finally:
                db.close()
    except Exception as error:
        return error_summary(error)


async def assrt(values):
    if not values.get("assrt_token"):
        return {"status": "unverified", "code": "assrt_token_required"}
    try:
        config = AppConfig(
            webdav_url="https://dav.example/dav/",
            scrape_paths=["/input"],
            assrt_token=values["assrt_token"],
            http_timeout_seconds=15,
        )
        async with AssrtProvider(config) as provider:
            last = None
            async for candidate in provider.automatic_candidates(
                values.get("subtitle_keyword") or "Fight.Club.1999"
            ):
                try:
                    body = await provider.download(
                        candidate["candidate_id"], candidate["index"], candidate["name"]
                    )
                    validate_subtitle(body, candidate["format"], 86400)
                    return {
                        "status": "passed",
                        "scope": "nas_container_real_assrt_keyword_format_only",
                        "subtitle_bytes": len(body),
                        "actual_video_matching": "unverified",
                    }
                except ProviderError as error:
                    if error.retryable or error.code in {
                        "assrt_token_required",
                        "assrt_token_invalid",
                    }:
                        raise
                    last = error
            raise last or ProviderError("subtitle_no_match")
    except Exception as error:
        return error_summary(error)


async def run(values):
    metadata, subtitles = await asyncio.gather(tmdb(values), assrt(values))
    return {"tmdb": metadata, "assrt": subtitles, "video_reads": 0, "remote_writes": 0}


if __name__ == "__main__":
    os.umask(0o077)
    print(json.dumps(asyncio.run(run(json.load(sys.stdin)))))
