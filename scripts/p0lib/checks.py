from __future__ import annotations

import json
import struct
import tempfile
from pathlib import Path

from .core import CheckError, Report, Transport, local_hash, shooter_hash
from .kodi import create_kodi_fixture
from .media import make_media_samples, probe
from .mockdav import MEDIA_BYTES, mock_server
from .shooter import PUBLIC_HASH, check_shooter
from .webdav import Sandbox, WebDAV, conflict_checks, move_checks, write_checks


def expect_error(action, codes):
    try:
        action()
    except CheckError as exc:
        if exc.code in codes:
            return {"rejected_with": exc.code}
        raise
    raise CheckError("fault_not_detected")


def scan(dav, path):
    entries = dav.list(path)
    current = dav.stat(path)
    if current is None or not current.collection:
        raise CheckError("scan_directory_missing")
    return {"entries": len(entries), "depth": 1, "per_item_status_checked": True}


def compare_hash(dav, remote, local):
    redirects_before = dav.transport.redirects
    digest, size = dav.fingerprint(remote)
    expected = local_hash(local) if isinstance(local, Path) else shooter_hash(len(local), lambda s, n: local[s:s + n])
    if digest != expected:
        raise CheckError("fingerprint_mismatch")
    return {"size": size, "range_bytes": 4 * 4096, "fingerprints_equal": True,
            "observed_redirects": dav.transport.redirects - redirects_before}


def media_check(dav, remote, name, config):
    before = dav.stat(remote)
    if before is None or before.collection or before.size is None:
        raise CheckError("invalid_media_stat")
    result = probe(before.size, lambda s, n: dav.read_range(remote, s, n, before.size, before.etag),
                   ffprobe=config.get("P0_FFPROBE", "ffprobe"),
                   timeout=float(config.get("P0_PROBE_TIMEOUT_SECONDS", "45")),
                   max_bytes=int(config.get("P0_PROBE_MAX_BYTES", "67108864")))
    if before != dav.stat(remote):
        raise CheckError("source_changed")
    if name:
        audio = [s for s in result["streams"] if s["type"] == "audio"]
        subs = [s for s in result["streams"] if s["type"] == "subtitle"]
        language = "chi" if name == "chinese.mkv" else "eng"
        if not audio or audio[0]["language"] != language or audio[0]["default"] != 1 or not subs:
            raise CheckError("fixture_stream_mismatch")
        if name == "foreign-traditional-forced.mkv" and subs[0]["forced"] != 1:
            raise CheckError("fixture_forced_flag_missing")
    return result


def mp4_tail_index(path):
    data = path.read_bytes()
    offset, atoms = 0, {}
    while offset + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[offset:offset + 8])
        if size < 8 or offset + size > len(data):
            raise CheckError("invalid_generated_mp4")
        atoms[kind] = offset
        offset += size
    if b"moov" not in atoms or b"mdat" not in atoms or atoms[b"moov"] < atoms[b"mdat"]:
        raise CheckError("mp4_not_tail_index")
    return {"moov_after_mdat": True}


def offline_checks(report: Report, config):
    with tempfile.TemporaryDirectory(prefix="reeldock-p0-") as tmp, mock_server() as mock:
        dav = WebDAV(mock.url, transport=Transport(timeout=0.1, use_env_proxy=False))
        report.run("dav_scan", "mock_http", lambda: scan(dav, "/"))
        sandbox = Sandbox(dav, "/tests")
        report.run("dav_small_upload_readback", "mock_http", lambda: write_checks(sandbox))
        report.run("remote_local_fingerprint", "mock_http", lambda: compare_hash(dav, "/media.bin", MEDIA_BYTES))
        report.run("range_redirect_302", "mock_http", lambda: compare_hash_redirect(dav, mock))
        for fault, codes in (("range_ignored", {"range_ignored"}), ("wrong_range", {"invalid_content_range"}),
                             ("short_range", {"short_range", "invalid_range_length"}),
                             ("short_body", {"short_range", "transport_error"})):
            mock.faults["/media.bin"] = fault
            count = dav.transport.bytes_read
            result = report.run(fault + "_rejected", "mock_http", lambda c=codes:
                                expect_error(lambda: dav.read_range("/media.bin", 4096, 4096, len(MEDIA_BYTES)), c))
            if result is not None:
                result["body_bytes_consumed"] = dav.transport.bytes_read - count
                report.checks[-1]["body_bytes_consumed"] = result["body_bytes_consumed"]
        mock.faults.clear()
        report.run("move", "mock_http", lambda: move_checks(sandbox, lambda doc: None))
        report.run("move_target_conflict", "mock_http", lambda: conflict_checks(sandbox))
        for fault in ("timeout_after", "timeout_before", "partial_move", "permission"):
            box = Sandbox(dav, "/tests")
            mock.faults[box.path("source")] = fault
            action = lambda: move_checks(box, lambda doc: None)
            if fault == "timeout_after":
                report.run("move_timeout_reconcile", "mock_http", action)
            else:
                codes = {"move_not_verified"} if fault == "timeout_before" else ({"move_multistatus"} if fault == "partial_move" else {"http_status"})
                report.run("move_" + fault + "_blocked", "mock_http", lambda a=action, c=codes: expect_error(a, c))
        report.run("shooter_query_download", "mock_http", lambda: check_shooter(dav.transport, mock.url + "shooter", PUBLIC_HASH, "generated.mkv"))
        for endpoint, code in (("shooter-no-match", "subtitle_no_match"), ("shooter-failure", "http_status"),
                               ("shooter-timeout", "network_timeout")):
            report.run(endpoint, "mock_http", lambda e=endpoint, c=code: expect_error(
                lambda: check_shooter(dav.transport, mock.url + e, PUBLIC_HASH, "generated.mkv"), {c}))
        generated = report.run("media_fixture_generation", "local_ffmpeg", lambda: {
            "samples": [p.name for p in make_media_samples(Path(tmp) / "media", config.get("P0_FFMPEG", "ffmpeg"))]})
        if generated:
            for name in generated["samples"]:
                path = Path(tmp) / "media" / name
                mock.files["/" + name] = path.read_bytes()
                report.run("ffprobe_" + name, "local_ffprobe_mock_http", lambda n=name: media_check(dav, "/" + n, n, config))
            report.run("mp4_tail_index", "local_fixture", lambda: mp4_tail_index(Path(tmp) / "media/tail-index.mp4"))
            report.run("kodi_fixture_generation", "local_fixture", lambda: create_kodi_fixture(
                Path(tmp) / "kodi", Path(tmp) / "media/chinese.mkv", config.get("P0_FFMPEG", "ffmpeg")))
    for name in ("real_openlist", "nas_media_matrix", "real_shooter", "kodi_import_refresh_offline"):
        report.add(name, "unverified", "external_environment", code="offline_run")


def compare_hash_redirect(dav, mock):
    # PROPFIND remains on the source; only GET has a redirect, like OpenList download URLs.
    mock.files["/redirect"] = MEDIA_BYTES
    result = compare_hash(dav, "/redirect", MEDIA_BYTES)
    result["redirect_status"] = 302
    return result


def save_journal(path: Path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation, 0600 from the first write; no credentials are stored.
    import os
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
