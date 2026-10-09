"""Repeatable P2 validation. No media bytes, probes, Shooter or MOVE in any mode.

mock: run the P2 deterministic integration/fault matrix.
tmdb: read real metadata/images using ignored .env.p2.
webdav: create a random child of the already agreed P0_TEST_ROOT and upload only
self-generated NFO/JPEG assets through the production uploader. Never DELETE.
"""

import argparse
import asyncio
import io
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from dotenv import dotenv_values
from PIL import Image
from sqlalchemy import select

from reeldock.cache import Cache, sha256
from reeldock.database import Database
from reeldock.domain import AppConfig, ConfigUpdate, Movie, ProviderError, Stage
from reeldock.exporter import export_nfo, validate_image
from reeldock.models import Asset, Package
from reeldock.pipeline import MoviePipeline
from reeldock.providers.tmdb import TMDBProvider
from reeldock.providers.webdav import WebDAVProvider
from reeldock.queue import Queue, base_verified
from reeldock.runtime import Runtime
from reeldock.security import SettingsStore, Vault


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (80, 120), "#163f4c").save(stream, "JPEG")
    return stream.getvalue()


async def tmdb(args):
    values = dotenv_values(args.tmdb_env, interpolate=False)
    if not values.get("P2_TMDB_TOKEN"):
        return {"status": "unverified", "code": "missing_tmdb_token"}, 2
    with tempfile.TemporaryDirectory(prefix="reeldock-p2-tmdb-") as directory:
        root = Path(directory)
        db = Database(root / "test.sqlite3")
        db.migrate()
        try:
            cache = Cache(db, root / "cache")
            config = AppConfig(
                webdav_url="https://dav.example/dav/",
                input_path="/incoming",
                output_path="/library",
                tmdb_token=values["P2_TMDB_TOKEN"],
                proxy_url=values.get("P2_PROXY_URL") or None,
            )
            async with TMDBProvider(config, cache) as provider:
                movie = await provider.movie_details(args.movie_id)
                actors = await provider.credits(args.movie_id)
                art = await provider.images(args.movie_id)
                downloaded = []
                for kind in ["poster", "fanart"]:
                    candidate = next((a for a in art if a.kind == kind), None)
                    if candidate is None:
                        raise ProviderError("tmdb_" + kind + "_missing")
                    data = validate_image(await provider.download_artwork(candidate))
                    downloaded.append({"kind": kind, "bytes": len(data), "sha256": sha256(data)})
                person = next((p for p in actors if p.profile), None)
                if person:
                    data = validate_image(await provider.download_artwork(person.profile))
                    downloaded.append({"kind": "actor", "bytes": len(data), "sha256": sha256(data)})
                # Same cached requests, deliberately repeated in one reproducible run.
                repeated = await provider.movie_details(args.movie_id)
                assert repeated == movie
                return {
                    "status": "passed",
                    "scope": "real_tmdb_read_only",
                    "tmdb_id": movie.external_id,
                    "original_language": movie.original_language,
                    "localized_text_present": bool(movie.title),
                    "images": downloaded,
                    "actors": len(actors),
                }, 0
        finally:
            db.close()


async def webdav(args):
    values = dotenv_values(args.webdav_env, interpolate=False)
    if not all(values.get(k) for k in ["P0_WEBDAV_URL", "P0_TEST_ROOT"]):
        return {"status": "unverified", "code": "missing_webdav_test_configuration"}, 2
    config = AppConfig(
        webdav_url=values["P0_WEBDAV_URL"],
        webdav_username=values.get("P0_WEBDAV_USERNAME") or "",
        webdav_password=values.get("P0_WEBDAV_PASSWORD"),
        input_path="/p2-test-input",
        output_path="/p2-test-output",
        http_timeout_seconds=30,
        redirect_hosts=[
            h.strip() for h in (values.get("P0_REDIRECT_HOSTS") or "").split(",") if h.strip()
        ],
    )
    test_id = ".reeldock-p2-" + uuid.uuid4().hex
    target = values["P0_TEST_ROOT"].rstrip("/") + "/" + test_id
    with tempfile.TemporaryDirectory(prefix="reeldock-p2-webdav-") as directory:
        root = Path(directory)
        db = Database(root / "test.sqlite3")
        db.migrate()
        try:
            store = SettingsStore(db, Vault(root))
            revision = store.save(ConfigUpdate(**config.model_dump()))
            queue = Queue(db, lease_seconds=360)
            queue.submit("movie_base", target, uuid.uuid4().hex, revision)
            claim = queue.claim("p2-verifier")
            queue.begin_step(claim)
            queue.finish_step(claim, Stage.MATCH, {"mode": "generated_test_assets_only"})
            queue.begin_step(claim)
            movie = Movie(
                source="tmdb",
                external_id="550",
                title="ReelDock P2 专用验证 & 示例",
                original_language="en",
                year=1999,
            )
            nfo = export_nfo(movie, [])
            contents = {
                "movie.nfo": ("nfo", nfo),
                "poster.jpg": ("poster", image_bytes()),
                "fanart.jpg": ("fanart", image_bytes()),
                ".actors/P2_Test_Actor.jpg": ("actor", image_bytes()),
            }
            with db.sessions.begin() as session:
                package = session.get(Package, claim.package_id)
                package.tmdb_id, package.original_language = "550", "en"
                package.base_required = list(contents)
                for path, (kind, _) in contents.items():
                    session.add(
                        Asset(package_id=package.id, relative_path=path, kind=kind, required=True)
                    )
            pipeline = MoviePipeline(queue, store, Runtime(data_dir=root))
            async with WebDAVProvider(config) as storage:
                parent = await storage.stat(values["P0_TEST_ROOT"])
                if not parent.is_dir:
                    raise ProviderError("storage_not_directory")
                await storage.mkdir(target)
                for path, (kind, data) in contents.items():

                    async def produce(body=data):
                        return body

                    await pipeline.upload(claim, storage, target, path, kind, produce, "550")
                with db.sessions.begin() as session:
                    verified = base_verified(session, session.get(Package, claim.package_id))
                    checks = [
                        {"kind": a.kind, "status": a.status, "sha256": a.sha256}
                        for a in session.scalars(select(Asset))
                    ]
                queue.finish_step(claim, Stage.BASE, {"scope": "asset_upload_only"})
                # Test conditional create rejection only against our newly generated sentinel.
                conflict = False
                try:
                    await storage.put(target + "/poster.jpg", image_bytes())
                except ProviderError as error:
                    conflict = error.code == "storage_conflict"
                if not conflict:
                    raise ProviderError("client_create_guard_failed")
                # Capability probe bypasses only the client existence guard, writing
                # IDENTICAL generated sentinel bytes. It never touches user assets.
                async with storage.response(
                    "PUT",
                    target + "/poster.jpg",
                    headers={"If-None-Match": "*"},
                    content=image_bytes(),
                ) as response:
                    condition_status = response.status_code
                conditional_supported = condition_status == 412

            return {
                "status": "passed" if verified else "failed",
                "scope": "real_webdav_asset_upload_only",
                "test_directory_name": test_id,
                "assets": checks,
                "client_create_conflict_rejected": conflict,
                "server_conditional_create_supported": conditional_supported,
                "server_condition_status": condition_status,
                "concurrent_external_writes_safe": False,
                "retained_for_manual_cleanup": True,
                "media_content_reads": 0,
                "probe_calls": 0,
                "subtitle_provider_calls": 0,
                "moves": 0,
            }, 0 if verified else 1
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["mock", "tmdb", "webdav"])
    parser.add_argument("--tmdb-env", default=".env.p2")
    parser.add_argument("--webdav-env", default=".env.p0")
    parser.add_argument("--movie-id", default="550")
    parser.add_argument("--output")
    args = parser.parse_args()
    started = time.monotonic()
    try:
        if args.mode == "mock":
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "backend/tests/test_p2_pipeline.py",
                    "backend/tests/test_p2_tmdb.py",
                    "backend/tests/test_p2_api.py",
                ],
                capture_output=True,
                text=True,
            )
            # No fixture bodies or local configuration are printed by this wrapper.
            result, code = (
                {
                    "status": "passed" if process.returncode == 0 else "failed",
                    "scope": "mock_p2_integration_and_fault_matrix",
                    "pytest_exit": process.returncode,
                },
                process.returncode,
            )
        else:

            async def bounded_run():
                async with asyncio.timeout(300):
                    return await (tmdb(args) if args.mode == "tmdb" else webdav(args))

            result, code = asyncio.run(bounded_run())
    except ProviderError as error:
        result, code = {"status": "failed", "code": error.code}, 1
    except Exception:
        result, code = (
            {"status": "failed", "code": "invalid_local_configuration_or_verifier_error"},
            1,
        )
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
