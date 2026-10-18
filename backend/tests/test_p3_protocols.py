import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from reeldock.domain import AppConfig, ProviderError
from reeldock.providers.shooter import ShooterProvider
from reeldock.subtitles import fingerprint, matches_external, simplified_track, validate_subtitle


def config(**changes):
    return AppConfig(webdav_url="https://dav.example/dav/", scrape_paths=["/in"], **changes)


async def test_fingerprint_offsets_match_local():
    data = bytes(i % 251 for i in range(131072))
    calls = []

    class Storage:
        async def read_range(self, path, start, length, size):
            calls.append(start)
            return data[start : start + length]

    media = SimpleNamespace(size=len(data), remote_path="/video.mkv")
    value = await fingerprint(Storage(), media)
    offsets = [4096, len(data) * 2 // 3, len(data) // 3, len(data) - 8192]
    assert calls == offsets
    assert value == ";".join(
        hashlib.md5(data[i : i + 4096], usedforsecurity=False).hexdigest() for i in offsets
    )


@pytest.mark.parametrize(
    "suffix,expected",
    [
        (".en.srt", True),
        (".zh-Hans.ass", True),
        (".English.srt", False),
        (".Another Movie.srt", False),
        (".idx", True),
    ],
)
def test_external_association(suffix, expected):
    assert matches_external("/pkg/Movie" + suffix, "/pkg/Movie.mkv") == expected
    assert not matches_external("/other/Movie" + suffix, "/pkg/Movie.mkv")


def test_encoding_delay_ass_and_timeline():
    source = "1\n00:00:00,000 --> 00:01:00,000\n影坞字幕\n"
    body, result = validate_subtitle(source.encode("gb18030"), "srt", 100, delay_ms=1200)
    assert "影坞".encode() in body and result["start_ms"] == 1200
    _, clipped = validate_subtitle(source.encode(), "srt", 100, delay_ms=-1)
    assert clipped["start_ms"] == 0 and clipped["end_ms"] == 59999
    ass = (
        "[Script Info]\nScriptType: v4.00+\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.00,0:01:00.00,Default,,0,0,0,,Hello\n"
    )
    _, result = validate_subtitle(ass.encode(), "ass", 100)
    assert result["cues"] == 1


async def test_shooter_requests_and_secure_links():
    calls = []

    def transport(request):
        calls.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if request.method == "POST":
            return httpx.Response(
                200,
                json=[
                    {"Delay": 123, "Files": [{"Ext": "srt", "Link": "https://shooter.cn/test.srt"}]}
                ],
            )
        return httpx.Response(200, content=b"1\n00:00:00,000 --> 00:00:01,000\nHello\n")

    async with ShooterProvider(config(), transport=httpx.MockTransport(transport)) as provider:
        candidates = await provider.search(";".join(["a" * 32] * 4))
        assert candidates[0].delay_ms == 123
        assert await provider.download(candidates[0])
        for url in [
            "http://shooter.cn/sub.srt",
            "https://evil.example/sub.srt",
            "https://user:pass@shooter.cn/sub.srt",
        ]:
            with pytest.raises(ProviderError, match="subtitle_host_not_allowed"):
                provider.allowed(url)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "body,code",
    [
        (b"<html>bad</html>", "subtitle_invalid_candidates"),
        (json.dumps([{"Delay": "20", "Files": []}]).encode(), "subtitle_invalid_candidates"),
    ],
)
async def test_bad_shooter_json(body, code):
    async with ShooterProvider(
        config(), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    ) as provider:
        with pytest.raises(ProviderError, match=code):
            await provider.search(";".join(["a" * 32] * 4))


def test_hans_label_does_not_accept_english_or_traditional_content():
    for text in ["English dialogue", "影塢電影測試對白"]:
        data = f"1\n00:00:00,100 --> 00:01:30,000\n{text}\n".encode()
        with pytest.raises(ProviderError, match="embedded_subtitle_not_simplified"):
            validate_subtitle(data, "srt", 100, full=True)


@pytest.mark.parametrize(
    "tag,title,flags,expected",
    [
        ("chi", "国配简体中字特效", {}, True),
        ("chi", "简英双语特效", {}, True),
        ("chi", "簡英雙語特效", {}, True),
        ("zho", "简中", {}, True),
        ("zh-Hans", "", {}, True),
        ("chi", "繁英双语特效", {}, False),
        ("chi", "繁体中字", {}, False),
        ("chi", "Chinese", {}, False),
        ("chi", "简英双语", {"forced": 1}, False),
        ("chi", "简英双语", {"comment": 1}, False),
        ("chi", "简体 Commentary", {}, False),
        ("chi", "简体 Forced", {}, False),
        ("chi", "简体 Signs & Songs", {}, False),
        ("chi", "简体仅标牌", {}, False),
        ("chi", "简体无对白", {}, False),
    ],
)
def test_simplified_subtitle_labels_exclude_partial_and_unconfirmed_tracks(
    tag, title, flags, expected
):
    assert simplified_track({"language": tag, "title": title, **flags}) is expected


@pytest.mark.parametrize(
    "tag,title", [("eng", "简英双语"), ("zh-Hant", "简英双语"), ("chi", "简英 繁英")]
)
def test_conflicting_bilingual_labels_need_review(tag, title):
    with pytest.raises(ProviderError, match="embedded_subtitle_ambiguous"):
        simplified_track({"language": tag, "title": title})


async def test_vobsub_pair_structure_and_demux(monkeypatch):
    from reeldock.probe import RangeGateway, validate_vobsub_pair

    calls = []

    async def run(self, executable, args, limit):
        calls.append((executable, args))
        self.check()
        return json.dumps(
            {
                "streams": [{"codec_type": "subtitle", "codec_name": "dvd_subtitle"}],
                "packets": [{"pts_time": "20.0"}],
            }
        ).encode()

    monkeypatch.setattr(RangeGateway, "run", run)
    idx = (
        b"# VobSub index file, v7\nid: en, index: 0\ntimestamp: 00:00:20:000, filepos: 000000000\n"
    )
    sub = b"\x00\x00\x01\xba" + b"\x00" * 100
    evidence = await validate_vobsub_pair(None, idx, sub, 100, lambda: None, config())
    assert evidence["packets"] == 1 and len(calls) == 1
    with pytest.raises(ProviderError, match="subtitle_invalid_vobsub"):
        await validate_vobsub_pair(None, idx, b"bad", 100, lambda: None, config())
    assert len(calls) == 1
