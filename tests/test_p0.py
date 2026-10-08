from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from scripts.p0 import main, validate_config
from scripts.p0lib.checks import compare_hash, media_check, mp4_tail_index, save_journal
from scripts.p0lib.core import CheckError, Report, Transport, load_env, local_hash, sample_positions, shooter_hash
from scripts.p0lib.kodi import check_kodi_observation, create_kodi_fixture
from scripts.p0lib.media import make_media_samples
from scripts.p0lib.mockdav import MEDIA_BYTES, SRT, mock_server
from scripts.p0lib.shooter import PUBLIC_HASH, check_shooter, validate_subtitle
from scripts.p0lib.webdav import Sandbox, WebDAV, conflict_checks, move_checks, reconcile, write_checks, xml_root


class ErrorAssertions:
    def error(self, code, action):
        with self.assertRaises(CheckError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class DavTests(ErrorAssertions, unittest.TestCase):
    def setUp(self):
        self.context = mock_server()
        self.mock = self.context.__enter__()
        self.dav = WebDAV(self.mock.url, "test-user", "test-password", Transport(timeout=0.08, use_env_proxy=False))

    def tearDown(self):
        self.context.__exit__(None, None, None)

    def box(self):
        return Sandbox(self.dav, "/tests")

    def test_scan_depth_and_optional_404_property(self):
        entries = self.dav.list("/")
        self.assertEqual({e.path for e in entries}, {"/", "/tests", "/media.bin"})
        self.assertEqual(self.mock.requests[-1][2]["Depth"], "1")
        self.assertIsNone(self.dav.stat("/absent"))

    def test_property_permission_is_not_silently_dropped(self):
        self.mock.faults["/"] = "property_permission"
        self.error("dav_property_failure", lambda: self.dav.list("/"))

    def test_http_permission(self):
        self.mock.faults["/tests"] = "permission"
        self.error("http_status", self.box)

    def test_unicode_put_and_full_readback(self):
        box = self.box()
        self.assertTrue(write_checks(box)["unicode_space_hash_percent"])
        self.assertIn(box.path("影坞 space #100% .nfo"), self.mock.files)

    def test_corrupt_put_rejected(self):
        box = self.box()
        self.mock.faults[box.path("bad")] = "corrupt_put"
        self.error("body_budget_exceeded", lambda: box.put("bad", b"hello"))

    def test_existing_file_never_overwritten(self):
        box = self.box()
        box.put("existing", b"original")
        self.error("http_status", lambda: box.put("existing", b"replace"))
        self.assertEqual(self.mock.files[box.path("existing")], b"original")

    def test_same_size_corruption_fails_sha256(self):
        box = self.box()
        self.mock.faults[box.path("bad")] = "corrupt_put_same_size"
        self.error("readback_hash_mismatch", lambda: box.put("bad", b"hello"))

    def test_mutation_cannot_escape_sandbox(self):
        box = self.box()
        for name in ("../x", "/x", "a/../x", "a//x"):
            self.error("outside_test_sandbox", lambda n=name: box.put(n, b"x"))
        self.error("outside_test_sandbox", lambda: box._mutate("PUT", "/media.bin", body=b"x"))
        self.error("dedicated_test_parent_required", lambda: Sandbox(self.dav, "/"))
        self.assertNotIn("DELETE", [r[0] for r in self.mock.requests])

    def test_repeated_run_uses_new_sandbox(self):
        first, second = self.box(), self.box()
        self.assertNotEqual(first.root, second.root)

    def test_existing_run_id_rejected(self):
        Sandbox(self.dav, "/tests", "a" * 32)
        self.error("test_run_already_exists", lambda: Sandbox(self.dav, "/tests", "a" * 32))

    def test_fingerprint_exactly_four_blocks(self):
        result = compare_hash(self.dav, "/media.bin", MEDIA_BYTES)
        gets = [r for r in self.mock.requests if r[0] == "GET"]
        self.assertEqual(len(gets), 4)
        self.assertEqual(result["range_bytes"], 16384)
        offsets = [int(r[2]["Range"].split("=")[1].split("-")[0]) for r in gets]
        self.assertEqual(offsets, [4096, 87384, 43692, 122885])

    def test_ignored_range_reads_zero_body_bytes(self):
        self.mock.faults["/media.bin"] = "range_ignored"
        self.error("range_ignored", lambda: self.dav.read_range("/media.bin", 4096, 4096, len(MEDIA_BYTES)))
        self.assertEqual(self.dav.transport.bytes_read, 0)

    def test_incorrect_content_range_reads_zero(self):
        self.mock.faults["/media.bin"] = "wrong_range"
        self.error("invalid_content_range", lambda: self.dav.read_range("/media.bin", 4096, 4096, len(MEDIA_BYTES)))
        self.assertEqual(self.dav.transport.bytes_read, 0)

    def test_short_range_length_rejected(self):
        self.mock.faults["/media.bin"] = "short_range"
        self.error("invalid_range_length", lambda: self.dav.read_range("/media.bin", 4096, 4096, len(MEDIA_BYTES)))

    def test_source_change_stops_fingerprint(self):
        self.mock.faults["/media.bin"] = "source_changes"
        self.error("range_http_error", lambda: self.dav.fingerprint("/media.bin"))

    def test_truncated_range_body_rejected(self):
        self.mock.faults["/media.bin"] = "short_body"
        self.error("short_range", lambda: self.dav.read_range("/media.bin", 4096, 4096, len(MEDIA_BYTES)))

    def test_same_origin_redirect_preserves_range_and_auth(self):
        result = self.dav.transport.request("GET", self.mock.url + "redirect", headers={
            **self.dav.headers, "Range": "bytes=4096-8191"}, statuses=(206,), max_bytes=4096,
            expected_range=(4096, 4096, len(MEDIA_BYTES)))
        self.assertEqual(result.body, MEDIA_BYTES[4096:8192])
        self.assertEqual(result.redirects, 1)
        self.assertIn("Authorization", self.mock.requests[-1][2])

    def test_cross_origin_requires_allowlist_and_strips_credentials(self):
        with mock_server() as target:
            self.mock.redirect_target = target.url + "media.bin"
            self.error("redirect_host_not_allowed", lambda: self.dav.transport.request("GET", self.mock.url + "cross-redirect"))
            transport = Transport(redirect_hosts=["127.0.0.1"], use_env_proxy=False)
            response = transport.request("GET", self.mock.url + "cross-redirect", headers={
                "Authorization": "private", "Cookie": "private", "If-Match": '"dav-etag"', "Range": "bytes=4096-8191"},
                statuses=(206,), max_bytes=4096, expected_range=(4096, 4096, len(MEDIA_BYTES)))
            self.assertEqual(len(response.body), 4096)
            headers = {k.lower(): v for k, v in target.requests[-1][2].items()}
            self.assertNotIn("authorization", headers)
            self.assertNotIn("cookie", headers)
            self.assertNotIn("if-match", headers)
            self.assertEqual(headers["range"], "bytes=4096-8191")

    def test_mutation_redirect_blocked(self):
        # POST response redirects cannot silently become GET or resend credentials elsewhere.
        class Fake:
            status = 302
            headers = {"Location": self.mock.url}
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch.object(self.dav.transport.opener, "open", return_value=Fake()):
            self.error("mutation_redirect_blocked", lambda: self.dav.transport.request("PUT", self.mock.url, body=b"x"))

    def test_move_success_and_journal_precedes_request(self):
        box = self.box()
        def journal(doc):
            self.assertFalse(any(r[0] == "MOVE" for r in self.mock.requests))
            self.assertEqual(doc["source"], box.path("source"))
        self.assertEqual(move_checks(box, journal)["state"], "moved_verified")
        self.assertNotIn(box.path("source"), self.mock.files)

    def test_move_conflict_preserves_both_directories(self):
        self.assertTrue(conflict_checks(self.box())["source_and_target_preserved"])

    def test_nonstandard_conflict_500_requires_both_sentinels(self):
        box = self.box()
        self.mock.faults[box.path("conflict-source")] = "conflict500"
        result = conflict_checks(box)
        self.assertEqual(result["http_status"], 500)
        self.assertFalse(result["standard_conflict_status"])
        self.assertTrue(result["source_and_target_preserved"])

    def test_timeout_after_move_reconciled_without_second_move(self):
        box = self.box()
        self.mock.faults[box.path("source")] = "timeout_after"
        result = move_checks(box, lambda doc: None)
        self.assertEqual(result["initial_response"], "ambiguous_transport")
        self.assertEqual(len([r for r in self.mock.requests if r[0] == "MOVE"]), 1)

    def test_timeout_before_move_does_not_retry_or_claim_success(self):
        box = self.box()
        self.mock.faults[box.path("source")] = "timeout_before"
        exc = self.error("move_not_verified", lambda: move_checks(box, lambda doc: None))
        self.assertEqual(exc.details["state"], "source_only")
        self.assertIn(box.path("source"), self.mock.files)

    def test_move_207_partial_failure_leaves_both_for_review(self):
        box = self.box()
        self.mock.faults[box.path("source")] = "partial_move"
        exc = self.error("move_multistatus", lambda: move_checks(box, lambda doc: None))
        self.assertEqual(exc.details["failure_statuses"], [403])
        self.assertIn(box.path("source"), self.mock.files)
        self.assertIn(box.path("moved"), self.mock.files)

    def test_reconcile_checks_identity_and_all_files(self):
        self.mock.files.update({"/moved": None, "/moved/identity.json": b"wrong", "/moved/a": b"one"})
        args = (self.dav, "/absent", "/moved", b"expected", {"a": b"one"})
        self.assertEqual(reconcile(*args, attempts=1), "move_unknown")
        self.mock.files["/moved/identity.json"] = b"expected"
        self.assertEqual(reconcile(*args, attempts=1), "moved_verified")
        self.mock.files["/moved/extra"] = b"unexpected"
        self.assertEqual(reconcile(*args, attempts=1), "move_unknown")

    def test_shooter_query_download_and_no_match(self):
        result = check_shooter(self.dav.transport, self.mock.url + "shooter", PUBLIC_HASH, "generated.mkv")
        self.assertEqual(result["download"]["format"], "srt")
        self.error("subtitle_no_match", lambda: check_shooter(self.dav.transport, self.mock.url + "shooter-no-match", PUBLIC_HASH, "test"))
        self.error("http_status", lambda: check_shooter(self.dav.transport, self.mock.url + "shooter-failure", PUBLIC_HASH, "test"))
        self.error("network_timeout", lambda: check_shooter(self.dav.transport, self.mock.url + "shooter-timeout", PUBLIC_HASH, "test"))


class LocalTests(ErrorAssertions, unittest.TestCase):
    def test_fingerprint_reference_offsets_and_local_equivalence(self):
        offsets = (4096, 87384, 43692, 122885)
        expected = ";".join(hashlib.md5(MEDIA_BYTES[p:p + 4096], usedforsecurity=False).hexdigest() for p in offsets)
        self.assertEqual(shooter_hash(len(MEDIA_BYTES), lambda s, n: MEDIA_BYTES[s:s + n]), expected)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.bin"
            path.write_bytes(MEDIA_BYTES)
            self.assertEqual(local_hash(path), expected)

    def test_hash_too_small_and_short_read(self):
        self.error("media_too_small", lambda: sample_positions(61439))
        self.error("short_range", lambda: shooter_hash(70000, lambda s, n: b"x"))

    def test_xml_entity_forbidden_and_external_href(self):
        self.error("unsafe_xml", lambda: xml_root(b'<!DOCTYPE a [<!ENTITY x "bad">]><a/>'))
        dav = WebDAV("https://example.invalid/dav/")
        for href in ("https://elsewhere.invalid/dav/x", "/dav2/x", "/dav/../x"):
            self.error("dav_href_outside_root", lambda h=href: dav._entry_path(h))

    def test_paths_are_encoded_once(self):
        dav = WebDAV("https://example.invalid/dav/")
        self.assertEqual(dav.url("/a%20 #影"), "https://example.invalid/dav/a%2520%20%23%E5%BD%B1")
        self.assertEqual(dav._entry_path("/dav/a%2520%20%23%E5%BD%B1"), "/a%20 #影")

    def test_subtitle_srt_utf8_utf16_gb18030(self):
        for encoding in ("utf-8", "utf-16", "gb18030"):
            data = SRT.decode().replace("Generated subtitle", "自建测试字幕").encode(encoding)
            self.assertEqual(validate_subtitle(data, "srt")["dialogue_cues"], 1)

    def test_subtitle_ass_and_bad_html_time_empty(self):
        ass = b"[Script Info]\nTitle: test\n[Events]\nFormat: Layer, Start, End, Text\nDialogue: 0,0:00:00.10,0:00:01.50,Hello, world\n"
        self.assertEqual(validate_subtitle(ass, ".ass")["dialogue_cues"], 1)
        self.error("subtitle_not_text", lambda: validate_subtitle(b"<html>expired</html>", "srt"))
        self.error("invalid_subtitle_cue", lambda: validate_subtitle(SRT.replace(b"01,500", b"00,050"), "srt"))
        self.error("subtitle_no_dialogue", lambda: validate_subtitle(b"text", "srt"))
        self.error("unsupported_subtitle_format", lambda: validate_subtitle(SRT, "zip"))

    def test_env_literal_no_execution_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"P0_SCAN_PATH": "/override"}):
            path = Path(tmp) / "env"
            path.write_text("# test\nP0_WEBDAV_PASSWORD='$(touch /tmp/not-executed)'\nP0_SCAN_PATH=/file\n")
            values = load_env(path)
            self.assertEqual(values["P0_SCAN_PATH"], "/override")
            self.assertEqual(values["P0_WEBDAV_PASSWORD"], "$(touch /tmp/not-executed)")
            path.write_text("export PASSWORD=bad\n")
            self.error("invalid_env_file", lambda: load_env(path))

    def test_invalid_budgets(self):
        for config in ({"P0_TIMEOUT_SECONDS": "nan"}, {"P0_PROBE_MAX_BYTES": "-1"}, {"P0_PROBE_TIMEOUT_SECONDS": "0"}):
            with self.assertRaises(CheckError):
                validate_config(config)

    def test_report_exit_codes_and_redaction(self):
        report = Report("test")
        report.run("private_failure", "mock", lambda: (_ for _ in ()).throw(ValueError("private URL")))
        self.assertEqual(report.exit_code(), 1)
        self.assertNotIn("private URL", json.dumps(report.document()))
        report = Report("test")
        self.assertEqual(report.exit_code(), 0)
        report.add("real_nas", "unverified", "external")
        self.assertEqual(report.exit_code(), 2)

    def test_journal_is_exclusive_private_and_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "intent.json"
            save_journal(path, {"source": "/generated"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["source"], "/generated")
            with self.assertRaises(FileExistsError):
                save_journal(path, {"source": "changed"})

    def test_incomplete_kodi_observations_are_not_passed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "observation.json"
            path.write_text('{"kodi_version":"21 test","first_import_nfo":true}')
            result = check_kodi_observation(path)
            self.assertEqual(result["observations"][0]["status"], "passed")
            self.assertEqual(result["observations"][-1]["status"], "unverified")

    def test_cli_missing_environment_returns_unverified_json(self):
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.p0.load_env", return_value={}):
            out = io.StringIO()
            with patch("sys.stdout", out), patch("sys.stderr", io.StringIO()):
                code = main(["live", "--output", str(Path(tmp) / "report.json")])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out.getvalue())["summary"]["failed"], 0)

    def test_cli_invalid_configuration_returns_failed_json(self):
        with tempfile.TemporaryDirectory() as tmp, patch("scripts.p0.load_env", return_value={"P0_TIMEOUT_SECONDS": "nan"}):
            out = io.StringIO()
            with patch("sys.stdout", out), patch("sys.stderr", io.StringIO()):
                code = main(["live", "--output", str(Path(tmp) / "report.json")])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out.getvalue())["checks"][0]["code"], "invalid_timeout_config")

    def test_invalid_write_flags_return_failure_not_unverified(self):
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["live", "--media-matrix"])
        self.assertEqual(caught.exception.code, 1)


class MediaTests(ErrorAssertions, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise unittest.SkipTest("FFmpeg/FFprobe required for generated media matrix")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.paths = make_media_samples(Path(cls.tmp.name) / "media")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_generated_mkv_mp4_matrix_via_http_range(self):
        # A dead external-proxy setting must not divert offline media traffic.
        with mock_server() as mock, patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:9", "no_proxy": ""}):
            dav = WebDAV(mock.url, transport=Transport(use_env_proxy=False))
            for path in self.paths:
                mock.files["/" + path.name] = path.read_bytes()
                with self.subTest(sample=path.name):
                    result = media_check(dav, "/" + path.name, path.name, {})
                    self.assertGreater(result["range_requests"], 0)
                    self.assertLessEqual(result["bytes_requested"], 67108864)
                    self.assertGreater(result["seconds"], 0)
            self.assertTrue(mp4_tail_index(self.paths[-1])["moov_after_mdat"])

    def test_probe_budget_stops_before_video_read(self):
        with mock_server() as mock:
            dav = WebDAV(mock.url, transport=Transport(use_env_proxy=False))
            mock.files["/sample"] = self.paths[0].read_bytes()
            self.error("probe_byte_budget", lambda: media_check(dav, "/sample", None, {"P0_PROBE_MAX_BYTES": "1"}))
            self.assertFalse(any(r[0] == "GET" for r in mock.requests))

    def test_kodi_fixture_has_jpeg_actors_and_no_remote_thumb_or_streamdetails(self):
        output = Path(self.tmp.name) / "kodi"
        result = create_kodi_fixture(output, self.paths[0])
        directory = next(p for p in output.iterdir() if p.is_dir())
        nfo = next(directory.glob("*.nfo"))
        tree = ET.parse(nfo)
        self.assertIsNone(tree.find(".//streamdetails"))
        self.assertIsNone(tree.find(".//thumb"))
        actor = tree.findtext("actor/name")
        self.assertTrue((directory / ".actors" / (actor.replace(" ", "_") + ".jpg")).read_bytes().startswith(b"\xff\xd8\xff"))
        self.assertEqual(result["kodi_import"], "unverified")


if __name__ == "__main__":
    unittest.main()
