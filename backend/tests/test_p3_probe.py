import shutil
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from reeldock.domain import AppConfig, ProviderError
from reeldock.probe import MediaProbe, RangeGateway
from reeldock.subtitles import default_audio, simplified_track, validate_subtitle


def configuration(**changes):
    return AppConfig(
        webdav_url="https://dav.example/dav/", input_path="/in", output_path="/out", **changes
    )


async def test_gateway_budget_and_opaque_url():
    calls = []

    class Storage:
        async def read_range(self, *args):
            calls.append(args)
            return b"x" * args[2]

    media = SimpleNamespace(size=100000, remote_path="/in/secret-name.mkv")
    g = RangeGateway(Storage(), media, configuration(probe_max_bytes=16384), lambda: None)
    async with g.open():
        assert "secret" not in g.url and g.url.startswith("http://127.0.0.1:")
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (await client.get(g.url, headers={"Range": "bytes=0-4095"})).status_code == 206
            assert (await client.get(g.url, headers={"Range": "bytes=0-99999"})).status_code == 502
            assert len(calls) == 1 and g.error.code == "probe_byte_budget"
            assert (await client.get(g.url + "wrong")).status_code == 404


async def test_gateway_range_ignored_stops_without_fallback():
    class Storage:
        async def read_range(self, *args):
            raise ProviderError("storage_range_ignored")

    g = RangeGateway(
        Storage(),
        SimpleNamespace(size=99999, remote_path="/video.mkv"),
        configuration(),
        lambda: None,
    )
    async with g.open():
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (await client.get(g.url, headers={"Range": "bytes=0-4095"})).status_code == 502
        assert g.error.code == "storage_range_ignored"


async def test_subprocess_timeout_and_output_budget():
    # The common runner kills and reaps on timeout/output excess, without logging stderr.
    g = RangeGateway(None, None, configuration(probe_timeout_seconds=1), lambda: None)
    import sys

    with pytest.raises(ProviderError, match="probe_timeout"):
        await g.run(sys.executable, ["-c", "import time; time.sleep(10)"], 100)
    g = RangeGateway(None, None, configuration(), lambda: None)
    with pytest.raises(ProviderError, match="probe_output_budget"):
        await g.run(sys.executable, ["-c", 'print("x" * 10000)'], 100)


async def test_actual_ffprobe_ffmpeg_over_local_gateway(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg not installed: real process compatibility remains unverified")
    subtitle = tmp_path / "test.srt"
    subtitle.write_text("1\n00:00:00,100 --> 00:00:01,800\n影坞自建测试字幕\n")
    sample = tmp_path / "sample.mkv"
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=12:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-i",
            str(subtitle),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:s",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-c:s",
            "srt",
            "-metadata:s:a:0",
            "language=eng",
            "-disposition:a:0",
            "default",
            "-metadata:s:s:0",
            "language=chi",
            "-metadata:s:s:0",
            "title=简体",
            str(sample),
        ],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0
    contents = sample.read_bytes()

    class Storage:
        async def read_range(self, path, start, length, size):
            return contents[start : start + length]

    media = SimpleNamespace(size=len(contents), remote_path="/in/movie.mkv")
    async with MediaProbe().session(Storage(), media, configuration(), lambda: None) as probe:
        data = await probe.probe()
        assert default_audio(data)["language"] == "en"
        stream = next(s for s in data["streams"] if s["type"] == "subtitle")
        assert simplified_track(stream)
        body = await probe.extract(stream["index"])
        _, evidence = validate_subtitle(body, "srt", data["duration"], full=True)
        assert evidence["cues"] == 1
        assert probe.gateway.bytes <= 67108864
