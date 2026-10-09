"""Repeatable P3 checks. Real WebDAV operations ONLY in P0_TEST_ROOT/random child.

mock runs deterministic tests. shooter uses the P0 public fingerprint; no video.
webdav uses synthetic bytes + Mock TMDB, real base readback/manifest/MOVE. It is
NOT a real film metadata match or NAS large-media probe compatibility test.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import select

from reeldock.database import Database
from reeldock.domain import AppConfig, ConfigUpdate, ProviderError
from reeldock.models import ArchiveIntent, Package, Task
from reeldock.providers.shooter import ShooterProvider
from reeldock.providers.webdav import WebDAVProvider
from reeldock.queue import Queue
from reeldock.runtime import Runtime
from reeldock.security import SettingsStore, Vault
from reeldock.subtitles import validate_subtitle
from reeldock.worker import Worker


async def shooter(args):
    from p0lib.shooter import PUBLIC_HASH

    values = dotenv_values(args.webdav_env, interpolate=False)
    config = AppConfig(
        webdav_url="https://dav.example/dav/",
        input_path="/in",
        output_path="/out",
        subtitle_download_hosts=["www.shooter.cn", "shooter.cn"],
        http_timeout_seconds=30,
    )
    async with ShooterProvider(config) as provider:
        candidates = await provider.search(
            values.get("P0_SHOOTER_HASH") or PUBLIC_HASH,
            {"filename": values.get("P0_SHOOTER_FILENAME") or "S05E09.mkv"},
        )
        if not candidates:
            raise ProviderError("subtitle_no_match")
        errors = []
        for candidate in candidates:
            try:
                body = await provider.download(candidate)
                # Public fingerprint's actual film duration is unknown: this check
                # proves parsing only, with a generous bound, never archive eligibility.
                _, evidence = validate_subtitle(
                    body, candidate.format, 24 * 3600, delay_ms=candidate.delay_ms
                )
                return {
                    "status": "passed",
                    "scope": "public_hash_parse_only",
                    "candidates": len(candidates),
                    "subtitle": evidence,
                }
            except ProviderError as error:
                errors.append(error.code)
        raise ProviderError(errors[-1])


async def webdav(args):
    values = dotenv_values(args.webdav_env, interpolate=False)
    if not values.get("P0_WEBDAV_URL") or not values.get("P0_TEST_ROOT"):
        return {"status": "unverified", "code": "missing_webdav_test_configuration"}
    # Explicitly no scan/input movie path from .env.p0 is used in this mode.
    test_id = ".reeldock-p3-" + uuid.uuid4().hex
    parent = values["P0_TEST_ROOT"].rstrip("/")
    root = parent + "/" + test_id
    config = AppConfig(
        webdav_url=values["P0_WEBDAV_URL"],
        webdav_username=values.get("P0_WEBDAV_USERNAME") or "",
        webdav_password=values.get("P0_WEBDAV_PASSWORD"),
        input_path=root + "/input",
        output_path=root + "/output",
        move_verified=True,
        stable_seconds=1,
        http_timeout_seconds=30,
        redirect_hosts=[
            h.strip() for h in (values.get("P0_REDIRECT_HOSTS") or "").split(",") if h.strip()
        ],
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))
    from p2_support import FakeTMDB

    retained = Path("reports/p3-runs") / test_id
    retained.mkdir(parents=True, mode=0o700)
    with nullcontext(retained) as folder:
        db = Database(folder / "test.sqlite3")
        db.migrate()
        try:
            store = SettingsStore(db, Vault(folder))
            store.save(ConfigUpdate(**config.model_dump()))
            queue = Queue(db)
            metadata = FakeTMDB()
            metadata.movies[0].original_language = "zh"
            worker = Worker(queue, store, Runtime(data_dir=folder), metadata_factory=metadata)
            package_path = config.input_path + "/Example (2020)"
            async with WebDAVProvider(config) as dav:
                if not (await dav.stat(parent)).is_dir:
                    raise ProviderError("test_parent_not_directory")
                for path in [root, config.input_path, config.output_path, package_path]:
                    await dav.mkdir(path)
                # This isolated tool alone seeds synthetic video-shaped bytes. The
                # production small-asset PUT method continues to reject video writes.
                async with dav.response(
                    "PUT",
                    package_path + "/Example.2020.1080p.mkv",
                    headers={"If-None-Match": "*"},
                    content=b"ReelDock generated P3 fixture\n" * 4096,
                ) as response:
                    if response.status_code not in {200, 201, 204}:
                        raise ProviderError("fixture_upload_failed")
                await worker.movies.scanner.scan(dav, config, 1, now=time.time() - 2)
                await worker.movies.scanner.scan(dav, config, 1)
            task_id = queue.submit("package_pipeline", package_path, "real-p3-test", 1)
            await worker.execute(queue.claim("p3-test"))
            with db.sessions.begin() as session:
                task = session.get(Task, task_id)
                package = session.get(Package, task.package_id)
                intent = session.scalar(select(ArchiveIntent))
                result = {
                    "status": "passed" if task.status == "completed" else "failed",
                    "scope": "dedicated_synthetic_package_mock_tmdb_real_webdav",
                    "test_id": test_id,
                    "subtitle_status": package.subtitle_status,
                    "archive_status": package.archive_status,
                    "error_code": task.error_code,
                    "intent_status": intent.status if intent else None,
                    "video_content_reads": 0,
                    "real_media_probe": "unverified",
                    "real_driver_version": "unverified",
                    "cleanup": "retained_no_delete",
                    "local_journal": "reports/p3-runs/" + test_id,
                }
            return result
        finally:
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["mock", "shooter", "webdav"])
    parser.add_argument("--webdav-env", default=".env.p0")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "mock":
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "backend/tests/test_p3_completion.py",
                    "backend/tests/test_p3_protocols.py",
                    "backend/tests/test_p3_probe.py",
                    "backend/tests/test_p3_api.py",
                ],
                check=False,
            )
            result = {
                "status": "passed" if completed.returncode == 0 else "failed",
                "scope": "mock_and_local_generated_media",
            }
        else:
            result = asyncio.run(shooter(args) if args.mode == "shooter" else webdav(args))
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
