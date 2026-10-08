#!/usr/bin/env python3
"""Dedicated P0 capability tests. This is not the production scraping pipeline."""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.p0lib.checks import (compare_hash, media_check, offline_checks, save_journal, scan)
from scripts.p0lib.core import CheckError, Report, Transport, load_env
from scripts.p0lib.kodi import check_kodi_observation, create_kodi_fixture
from scripts.p0lib.media import make_media_samples
from scripts.p0lib.mockdav import MEDIA_BYTES
from scripts.p0lib.shooter import PUBLIC_HASH, check_shooter
from scripts.p0lib.webdav import Sandbox, WebDAV, conflict_checks, move_checks, reconcile, write_checks


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def configured_dav(config):
    if not config.get("P0_WEBDAV_URL"):
        raise CheckError("webdav_not_configured")
    return WebDAV(config["P0_WEBDAV_URL"], config.get("P0_WEBDAV_USERNAME", ""),
                  config.get("P0_WEBDAV_PASSWORD", ""), Transport(
                      timeout=float(config.get("P0_TIMEOUT_SECONDS", "15")),
                      redirect_hosts=config.get("P0_REDIRECT_HOSTS", "").split(",")))


def live_checks(report, args, config):
    if config.get("P0_WEBDAV_URL"):
        dav = configured_dav(config)
        report.run("dav_scan", "real_webdav", lambda: scan(dav, config.get("P0_SCAN_PATH", "/")))
        if args.allow_write:
            box = None
            def setup():
                nonlocal box
                box = Sandbox(dav, config.get("P0_TEST_ROOT", ""))
                # The random ID is safe to print; the user's private parent path is not.
                return {"run_id": box.root.rsplit("/", 1)[-1]}
            report.run("test_sandbox", "real_webdav", setup)
            if box:
                report.run("dav_small_upload_readback", "real_webdav", lambda: write_checks(box))
                def generated_hash():
                    box.put("range-sample.bin", MEDIA_BYTES)
                    return compare_hash(dav, box.path("range-sample.bin"), MEDIA_BYTES)
                report.run("generated_range_fingerprint", "real_webdav", generated_hash)
                journal_path = args.output.parent / ("move-intent-" + uuid.uuid4().hex + ".json")
                report.run("move", "real_webdav", lambda: move_checks(box, lambda doc: save_journal(journal_path, doc)))
                report.run("move_target_conflict", "real_webdav", lambda: conflict_checks(box))
                if args.media_matrix:
                    with tempfile.TemporaryDirectory(prefix="reeldock-p0-media-") as tmp:
                        samples = make_media_samples(Path(tmp), config.get("P0_FFMPEG", "ffmpeg"))
                        for sample in samples:
                            def check_sample(p=sample):
                                box.put(p.name, p.read_bytes())
                                return media_check(dav, box.path(p.name), p.name, config)
                            report.run("nas_ffprobe_" + sample.name, "real_ffprobe_webdav_synthetic_media", check_sample)
                report.add("real_move_timeout_injection", "unverified", "real_webdav", code="not_injected_on_nas")
        else:
            report.add("dav_write_move", "unverified", "real_webdav", code="allow_write_not_enabled")
        remote = config.get("P0_REMOTE_MEDIA")
        if remote:
            if config.get("P0_LOCAL_MEDIA"):
                report.run("remote_local_fingerprint", "real_webdav", lambda: compare_hash(dav, remote, Path(config["P0_LOCAL_MEDIA"])))
            else:
                report.run("remote_fingerprint", "real_webdav", lambda: {"size": dav.fingerprint(remote)[1], "range_bytes": 16384})
                report.add("remote_local_fingerprint", "unverified", "real_webdav", code="local_copy_not_configured")
            if args.probe:
                report.run("dedicated_media_probe", "real_ffprobe_webdav", lambda: media_check(dav, remote, None, config))
            else:
                report.add("dedicated_media_probe", "unverified", "real_ffprobe_webdav", code="probe_not_enabled")
        else:
            report.add("representative_user_media", "unverified", "real_webdav", code="remote_sample_not_configured")
    else:
        report.add("real_openlist", "unverified", "external_environment", code="webdav_not_configured")
    if args.shooter:
        transport = Transport(timeout=float(config.get("P0_TIMEOUT_SECONDS", "15")),
                              redirect_hosts=config.get("P0_REDIRECT_HOSTS", "").split(","))
        report.run("shooter_query_download", "real_shooter", lambda: check_shooter(
            transport, config.get("P0_SHOOTER_URL", "https://www.shooter.cn/api/subapi.php"),
            config.get("P0_SHOOTER_HASH") or PUBLIC_HASH, config.get("P0_SHOOTER_FILENAME") or "S05E09.mkv"))
    else:
        report.add("real_shooter", "unverified", "external_environment", code="shooter_not_enabled")
    report.add("kodi_import_refresh_offline", "unverified", "external_environment", code="use_kodi_fixtures_and_kodi_check")


def validate_config(config):
    for key, default in (("P0_TIMEOUT_SECONDS", "15"), ("P0_PROBE_TIMEOUT_SECONDS", "45")):
        try:
            value = float(config.get(key) or default)
            if not math.isfinite(value) or not 0 < value <= 60:
                raise ValueError
        except ValueError:
            raise CheckError("invalid_timeout_config") from None
    try:
        value = int(config.get("P0_PROBE_MAX_BYTES") or "67108864")
        if not 0 < value <= 1024 ** 3:
            raise ValueError
    except ValueError:
        raise CheckError("invalid_byte_budget_config") from None


def main(argv=None):
    parser = ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env-file", type=Path, help="Literal P0_KEY=value file; never executed")
    common.add_argument("--output", type=Path, help="Private JSON report (default reports/p0-MODE.json)")
    commands = parser.add_subparsers(dest="mode", required=True)
    commands.add_parser("offline", parents=[common], help="Loopback faults + generated FFmpeg samples; no internet")
    live = commands.add_parser("live", parents=[common], help="Configured external capability tests")
    live.add_argument("--allow-write", action="store_true", help="Create new sandbox under existing P0_TEST_ROOT; never DELETE")
    live.add_argument("--probe", action="store_true", help="Explicit remote FFprobe capability test with byte/time budget")
    live.add_argument("--shooter", action="store_true", help="Send configured or public hash; download and validate one subtitle")
    live.add_argument("--media-matrix", action="store_true", help="With --allow-write, upload/probe four tiny generated media samples in sandbox")
    fixtures = commands.add_parser("kodi-fixtures", parents=[common], help="Generate unique synthetic movie + NFO + JPG .actors")
    fixtures.add_argument("--directory", type=Path, default=Path("fixtures/kodi"))
    kodi = commands.add_parser("kodi-check", parents=[common], help="Check a human's actual Kodi observations (not automated UI evidence)")
    kodi.add_argument("--observation", type=Path, required=True)
    recover = commands.add_parser("reconcile", parents=[common], help="Read-only reconciliation of a saved MOVE intent")
    recover.add_argument("--journal", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "live" and args.media_matrix and not args.allow_write:
        parser.error("--media-matrix requires --allow-write")
    args.output = args.output or Path("reports") / ("p0-" + args.mode + ".json")
    report = Report(args.mode)
    try:
        env_path = args.env_file or (Path(".env.p0") if Path(".env.p0").exists() else None)
        config = load_env(env_path)
        validate_config(config)
        if args.mode == "offline":
            offline_checks(report, config)
        elif args.mode == "live":
            live_checks(report, args, config)
        elif args.mode == "kodi-fixtures":
            def generate():
                samples = make_media_samples(args.directory / "generated", config.get("P0_FFMPEG", "ffmpeg"))
                return create_kodi_fixture(args.directory, samples[0], config.get("P0_FFMPEG", "ffmpeg"))
            report.run("kodi_fixture_generation", "local_fixture", generate)
            report.add("kodi_import_refresh_offline", "unverified", "external_environment", code="fixture_generation_is_not_kodi_verification")
        elif args.mode == "kodi-check":
            observations = report.run("kodi_observation_record", "manual_attestation", lambda: check_kodi_observation(args.observation))
            if observations:
                for item in observations["observations"]:
                    report.add("kodi_" + item["check"], item["status"], "manual_attestation")
        else:
            def recovery():
                doc = json.loads(args.journal.read_text())
                state = reconcile(configured_dav(config), doc["source"], doc["target"], doc["identity"].encode(),
                                  {name: value.encode() for name, value in doc["files"].items()})
                if state != "moved_verified":
                    raise CheckError("move_not_verified", state=state)
                return {"state": state, "read_only": True}
            report.run("move_reconcile", "real_webdav", recovery)
    except CheckError as exc:
        report.add("configuration_or_setup", "failed", "local", code=exc.code, **exc.details)
    except Exception:
        report.add("configuration_or_setup", "failed", "local", code="unexpected_error")
    try:
        report.write(args.output)
    except OSError:
        report.add("report_write", "failed", "local", code="report_write_failed")
    print(json.dumps(report.document(), ensure_ascii=False, indent=2))
    print("P0 checks: " + ", ".join(f"{k}={v}" for k, v in report.document()["summary"].items()) +
          ". Full NAS/Kodi acceptance is tracked in docs/compatibility.md.", file=sys.stderr)
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
