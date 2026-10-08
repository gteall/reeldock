from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import CheckError


@contextmanager
def range_gateway(size, reader, max_bytes, timeout):
    """Credentials stay in the reader. FFprobe sees only a temporary loopback token."""
    token = "/" + secrets.token_hex(24)
    lock = threading.Lock()
    state = {"bytes": 0, "requests": 0, "error": None}
    deadline = time.monotonic() + timeout

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_HEAD(self):
            if self.path != token:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            if self.path != token:
                self.send_error(404)
                return
            value = self.headers.get("Range", "bytes=0-")
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", value)
            if not match:
                self.send_error(416)
                return
            start = int(match[1])
            end = min(int(match[2]) if match[2] else size - 1, size - 1)
            if start > end or start >= size:
                self.send_error(416)
                return
            try:
                # Reserve the budget before reading; concurrent requests cannot overspend it.
                with lock:
                    count = min(end - start + 1, 1024 * 1024)
                    if time.monotonic() >= deadline:
                        raise CheckError("probe_timeout")
                    if state["bytes"] + count > max_bytes:
                        raise CheckError("probe_byte_budget")
                    state["bytes"] += count
                    state["requests"] += 1
                data = reader(start, count)
                if len(data) != count:
                    raise CheckError("short_range")
                self.send_response(206)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{start + count - 1}/{size}")
                self.send_header("Content-Length", str(count))
                self.end_headers()
                self.wfile.write(data)
            except CheckError as exc:
                state["error"] = exc.code
                state["error_details"] = exc.details
                self.send_error(502)
            except (ConnectionError, BrokenPipeError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}{token}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def probe(size, reader, ffprobe="ffprobe", timeout=45, max_bytes=64 * 1024 * 1024):
    executable = shutil.which(ffprobe)
    if not executable:
        raise CheckError("ffprobe_missing")
    started = time.monotonic()
    with range_gateway(size, reader, max_bytes, timeout) as (url, state):
        command = [executable, "-v", "error", "-rw_timeout", str(int(timeout * 1_000_000)),
                   "-show_streams", "-show_format", "-of", "json", url]
        try:
            # FFprobe only contacts loopback. Proxy settings belong to the upstream
            # reader, not this subprocess, including when offline tests are run.
            child_env = {k: v for k, v in os.environ.items()
                         if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
            completed = subprocess.run(command, capture_output=True, timeout=timeout, check=False, env=child_env)
        except subprocess.TimeoutExpired:
            raise CheckError("probe_timeout", bytes_requested=state["bytes"]) from None
        if state["error"]:
            raise CheckError(state["error"], bytes_requested=state["bytes"], **state.get("error_details", {}))
        if completed.returncode:
            raise CheckError("ffprobe_failed", returncode=completed.returncode)
        try:
            data = json.loads(completed.stdout)
        except ValueError:
            raise CheckError("invalid_ffprobe_json") from None
    streams = []
    for stream in data.get("streams", []):
        tags = stream.get("tags", {})
        streams.append({"index": stream.get("index"), "type": stream.get("codec_type"),
                        "codec": stream.get("codec_name"), "language": tags.get("language"),
                        "default": stream.get("disposition", {}).get("default", 0),
                        "forced": stream.get("disposition", {}).get("forced", 0)})
    if not streams:
        raise CheckError("no_media_streams")
    return {"streams": streams, "duration": data.get("format", {}).get("duration"),
            "seconds": round(time.monotonic() - started, 3),
            "bytes_requested": state["bytes"], "range_requests": state["requests"]}


def local_reader(path: Path):
    def read(start, count):
        with path.open("rb") as handle:
            handle.seek(start)
            return handle.read(count)
    return read


def make_media_samples(output: Path, ffmpeg="ffmpeg"):
    executable = shutil.which(ffmpeg)
    if not executable:
        raise CheckError("ffmpeg_missing")
    output.mkdir(parents=True, exist_ok=True)
    samples = []
    for name, audio_language, subtitle_language, forced in (
        ("chinese.mkv", "chi", "chi", False),
        ("foreign-simplified.mkv", "eng", "chi", False),
        ("foreign-traditional-forced.mkv", "eng", "chi", True),
        ("tail-index.mp4", "eng", "eng", False),
    ):
        path = output / name
        title = "繁体" if forced else ("简体" if subtitle_language == "chi" else "English")
        subtitle = output / (name + ".srt")
        text = "影塢自建測試字幕" if forced else ("影坞自建测试字幕" if subtitle_language == "chi" else "ReelDock generated test subtitle")
        subtitle.write_text(f"1\n00:00:00,100 --> 00:00:01,500\n{text}\n", encoding="utf-8")
        command = [executable, "-nostdin", "-v", "error", "-n", "-f", "lavfi", "-i",
                   "color=c=black:s=160x90:r=12:d=2", "-f", "lavfi", "-i",
                   "sine=frequency=440:duration=2", "-i", str(subtitle),
                   "-map", "0:v", "-map", "1:a", "-map", "2:s",
                   "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "96k",
                   "-c:s", "mov_text" if path.suffix == ".mp4" else "srt",
                   "-metadata:s:a:0", f"language={audio_language}", "-disposition:a:0", "default",
                   "-metadata:s:s:0", f"language={subtitle_language}",
                   "-metadata:s:s:0", f"title={title}", "-disposition:s:0", "forced" if forced else "0",
                   str(path)]
        if not path.exists():
            try:
                result = subprocess.run(command, capture_output=True, timeout=30, check=False)
            except subprocess.TimeoutExpired:
                raise CheckError("fixture_generation_timeout") from None
            if result.returncode:
                raise CheckError("fixture_generation_failed")
        samples.append(path)
    return samples
