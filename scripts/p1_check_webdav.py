"""Read-only HTTPX Provider regression with existing ignored P0 credentials.

Run with `uv run python scripts/p1_check_webdav.py --env-file .env.p0`.
Only PROPFIND is issued. No configuration is copied into the application database.
"""

import argparse
import asyncio
import json

from dotenv import dotenv_values

from reeldock.domain import AppConfig, ProviderError
from reeldock.providers.webdav import WebDAVProvider


async def check(values):
    required = ["P0_WEBDAV_URL", "P0_SCAN_PATH", "P0_TEST_ROOT"]
    if any(not values.get(key) for key in required):
        return {"status": "unverified", "code": "missing_p0_read_only_configuration"}, 2
    # The directory relationship check belongs to the future app configuration.
    # P0_SCAN_PATH and P0_TEST_ROOT are independent read-only test subjects here.
    config = AppConfig(
        webdav_url=values["P0_WEBDAV_URL"],
        webdav_username=values.get("P0_WEBDAV_USERNAME") or "",
        webdav_password=values.get("P0_WEBDAV_PASSWORD"),
        input_path="/p1-config-input",
        output_path="/p1-config-output",
        http_timeout_seconds=float(values.get("P0_TIMEOUT_SECONDS") or 15),
    )
    results = []
    async with WebDAVProvider(config) as storage:
        for label, key in [("scan_directory", "P0_SCAN_PATH"), ("test_parent", "P0_TEST_ROOT")]:
            try:
                entries = await storage.list(values[key])
                results.append({"check": label, "status": "passed", "entries": len(entries)})
            except ProviderError as error:
                results.append({"check": label, "status": "failed", "code": error.code})
    failed = any(item["status"] == "failed" for item in results)
    return {
        "status": "failed" if failed else "passed",
        "mode": "read_only",
        "checks": results,
        "media_content_reads": 0,
        "writes": 0,
        "moves": 0,
    }, int(failed)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env.p0")
    args = parser.parse_args()
    try:
        result, code = asyncio.run(check(dotenv_values(args.env_file, interpolate=False)))
    except Exception:
        result, code = {"status": "failed", "code": "invalid_local_configuration"}, 1
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
