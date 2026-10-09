"""Ephemeral loopback Range gateway. Only a gated subtitle job may instantiate it."""

import asyncio
import json
import math
import os
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager

from reeldock.domain import ProviderError


class RangeGateway:
    def __init__(self, storage, media, config, check):
        self.storage, self.media, self.config, self.check = storage, media, config, check
        self.token = "/" + secrets.token_hex(24)
        self.bytes = self.requests = 0
        self.deadline = time.monotonic() + config.probe_timeout_seconds
        self.error = None
        self.clients = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.clients.add(task)
        try:
            async with asyncio.timeout(max(0.01, self.deadline - time.monotonic())):
                raw = await reader.readuntil(b"\r\n\r\n")
                if len(raw) > 8192:
                    raise ProviderError("probe_gateway_request_invalid")
                lines = raw.decode("ascii").split("\r\n")
                method, path, _ = lines[0].split(" ")
                if path != self.token or method not in {"GET", "HEAD"}:
                    writer.write(
                        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                    await writer.drain()
                    return
                self.check()
                size = self.media.size
                if method == "HEAD":
                    writer.write(
                        (
                            f"HTTP/1.1 200 OK\r\nContent-Length: {size}\r\n"
                            "Accept-Ranges: bytes\r\nConnection: close\r\n\r\n"
                        ).encode()
                    )
                else:
                    headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
                    value = next(
                        (v.strip() for k, v in headers.items() if k.lower() == "range"), "bytes=0-"
                    )
                    match = re.fullmatch(r"bytes=(\d+)-(\d*)", value)
                    if not match:
                        raise ProviderError("storage_invalid_range")
                    start = int(match[1])
                    end = min(int(match[2]) if match[2] else size - 1, size - 1)
                    count = min(end - start + 1, 1024 * 1024)
                    if start < 0 or count <= 0 or start >= size:
                        raise ProviderError("storage_invalid_range")
                    if self.error:
                        raise self.error
                    # Reservation precedes the await: concurrent readers share one budget.
                    if self.bytes + count > self.config.probe_max_bytes:
                        raise ProviderError("probe_byte_budget")
                    self.bytes += count
                    self.requests += 1
                    body = await self.storage.read_range(self.media.remote_path, start, count, size)
                    self.check()
                    if len(body) != count:
                        raise ProviderError("storage_short_read")
                    writer.write(
                        (
                            f"HTTP/1.1 206 Partial Content\r\nContent-Length: {count}\r\n"
                            f"Content-Range: bytes {start}-{start + count - 1}/{size}\r\n"
                            "Accept-Ranges: bytes\r\nConnection: close\r\n\r\n"
                        ).encode()
                        + body
                    )
                await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = (
                exc if isinstance(exc, ProviderError) else ProviderError("probe_gateway_failed")
            )
            writer.write(
                b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
        finally:
            writer.close()
            self.clients.discard(task)

    @asynccontextmanager
    async def open(self):
        server = await asyncio.start_server(self.handle, "127.0.0.1", 0, limit=8192)
        self.url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}{self.token}"
        try:
            yield self
        finally:
            server.close()
            await server.wait_closed()
            tasks = list(self.clients)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, executable, args, limit):
        self.check()
        path = shutil.which(executable)
        if not path:
            raise ProviderError(executable + "_missing")
        child = None

        async def bounded(stream):
            result = bytearray()
            while chunk := await stream.read(8192):
                result.extend(chunk)
                if len(result) > limit:
                    raise ProviderError("probe_output_budget")
            return bytes(result)

        tasks = []
        try:
            async with asyncio.timeout(max(0.01, self.deadline - time.monotonic())):
                # No WebDAV URL, cookies, credentials, or proxy environment reaches the child.
                child = await asyncio.create_subprocess_exec(
                    path,
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    stdin=asyncio.subprocess.DEVNULL,
                    env={
                        k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT", "LANG"}
                    },
                )
                tasks = [
                    asyncio.create_task(bounded(child.stdout)),
                    asyncio.create_task(bounded(child.stderr)),
                ]
                output, _ = await asyncio.gather(*tasks)
                await child.wait()
                if self.error:
                    raise self.error
                if child.returncode:
                    raise ProviderError(executable + "_failed")
                self.check()
                return output
        except TimeoutError:
            raise ProviderError("probe_timeout", retryable=True) from None
        finally:
            if child and child.returncode is None:
                child.kill()
                await child.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class MediaProbe:
    @asynccontextmanager
    async def session(self, storage, media, config, check):
        async with RangeGateway(storage, media, config, check).open() as gateway:
            yield ProbeSession(gateway)


class ProbeSession:
    def __init__(self, gateway):
        self.gateway = gateway

    async def probe(self):
        g = self.gateway
        body = await g.run(
            "ffprobe",
            [
                "-v",
                "error",
                "-protocol_whitelist",
                "http,tcp",
                "-format_whitelist",
                "matroska,webm,mov,avi,mpegts,mpeg,asf",
                "-rw_timeout",
                str(g.config.probe_timeout_seconds * 1000000),
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                g.url,
            ],
            2 * 1024 * 1024,
        )
        try:
            data = json.loads(body)
            streams = [
                {
                    "index": int(s["index"]),
                    "type": s.get("codec_type"),
                    "codec": s.get("codec_name"),
                    "language": s.get("tags", {}).get("language"),
                    "title": (s.get("tags", {}).get("title") or "")[:256],
                    "default": s.get("disposition", {}).get("default", 0),
                    "forced": s.get("disposition", {}).get("forced", 0),
                    "comment": s.get("disposition", {}).get("comment", 0),
                }
                for s in data["streams"]
            ]
            try:
                duration = float(data.get("format", {}).get("duration", 0))
            except (ValueError, TypeError):
                duration = None
            if duration is not None and (duration <= 0 or not math.isfinite(duration)):
                duration = None
            if not streams:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ProviderError("invalid_ffprobe_json") from None
        return {
            "streams": streams,
            "duration": duration,
            "bytes_requested": g.bytes,
            "range_requests": g.requests,
        }

    async def extract(self, index):
        g = self.gateway
        return await g.run(
            "ffmpeg",
            [
                "-nostdin",
                "-v",
                "error",
                "-protocol_whitelist",
                "http,tcp",
                "-format_whitelist",
                "matroska,webm,mov,avi,mpegts,mpeg,asf",
                "-rw_timeout",
                str(g.config.probe_timeout_seconds * 1000000),
                "-i",
                g.url,
                "-map",
                f"0:{int(index)}",
                "-f",
                "srt",
                "-",
            ],
            2 * 1024 * 1024,
        )


async def validate_vobsub_pair(probe, idx, sub, duration, check, config):
    """Read-only local demux of a bounded IDX/SUB pair, never a video download."""
    import tempfile
    from pathlib import Path

    if len(idx) > 2 * 1024 * 1024 or len(sub) > 20 * 1024 * 1024:
        raise ProviderError("subtitle_size_invalid")
    try:
        text = idx.decode("utf-8-sig")
    except UnicodeError:
        raise ProviderError("subtitle_invalid_vobsub") from None
    positions = re.findall(
        r"timestamp:\s*(\d{2}):(\d{2}):(\d{2}):(\d{3}),\s*filepos:\s*([0-9a-fA-F]+)", text
    )
    if (
        not text.startswith("# VobSub index file")
        or not positions
        or not re.search(r"(?m)^id:\s*[a-z]{2},\s*index:\s*\d+", text)
    ):
        raise ProviderError("subtitle_invalid_vobsub")
    end = 0
    for hour, minute, second, ms, offset in positions:
        position = int(offset, 16)
        timestamp = int(hour) * 3600 + int(minute) * 60 + int(second) + int(ms) / 1000
        if (
            int(minute) > 59
            or int(second) > 59
            or sub[position : position + 4] != b"\x00\x00\x01\xba"
        ):
            raise ProviderError("subtitle_invalid_vobsub")
        end = max(end, timestamp)
    if not duration or end > duration + max(10, duration * 0.1):
        raise ProviderError("subtitle_timeline_mismatch")
    check()
    # This gateway instance supplies only the common subprocess sandbox/budgets;
    # it is not opened and cannot expose or request remote video bytes.
    runner = RangeGateway(None, None, config, check)
    with tempfile.TemporaryDirectory(prefix="reeldock-vobsub-") as folder:
        root = Path(folder)
        (root / "subtitle.idx").write_bytes(idx)
        (root / "subtitle.sub").write_bytes(sub)
        raw = await runner.run(
            "ffprobe",
            [
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-show_packets",
                "-show_streams",
                "-show_entries",
                "packet=pts_time:stream=codec_type,codec_name",
                "-of",
                "json",
                str(root / "subtitle.idx"),
            ],
            2 * 1024 * 1024,
        )
    try:
        data = json.loads(raw)
        if not data.get("packets") or not any(
            s.get("codec_type") == "subtitle" and s.get("codec_name") == "dvd_subtitle"
            for s in data.get("streams", [])
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ProviderError("subtitle_invalid_vobsub") from None
    return {
        "format": "idx/sub",
        "packets": len(data["packets"]),
        "end_seconds": end,
        "sync": "coarse_timeline_only",
    }
