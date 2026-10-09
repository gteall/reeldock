"""Conservative subtitle evidence. Parsing proves structure, not dialogue synchronisation."""

import hashlib
import math
import re
from pathlib import PurePosixPath

import pycountry
import pysubs2
from charset_normalizer import from_bytes
from opencc import OpenCC

from reeldock.domain import ProviderError

MAX_SUBTITLE = 2 * 1024 * 1024
CHINESE = {"zh", "zho", "chi", "cmn", "yue", "cn", "chs", "cht"}
COMMENTARY = re.compile(r"commentary|description|解说|解說|评论|評論", re.I)
TO_SIMPLIFIED = OpenCC("t2s")


def language(value):
    code = (value or "").strip().lower().replace("_", "-").split("-")[0]
    if code in CHINESE:
        return "zh"
    if code in {"und", "mul", "zxx", ""}:
        return None
    field = "alpha_2" if len(code) == 2 else "alpha_3"
    found = pycountry.languages.get(**{field: code}) or pycountry.languages.get(bibliographic=code)
    return (getattr(found, "alpha_2", None) or found.alpha_3) if found else None


def original_class(value, chinese):
    code = (value or "").strip().lower()
    if code in chinese:
        return "chinese"
    # TMDB fields must be language codes, not guessed from titles or country names.
    if not re.fullmatch("[a-z]{2,3}", code) or language(code) is None:
        return "unknown"
    return "foreign"


def default_audio(probe):
    audio = [s for s in probe["streams"] if s["type"] == "audio"]
    defaults = [s for s in audio if s.get("default")]
    if not audio or len(defaults) > 1:
        raise ProviderError("default_audio_ambiguous")
    selected = defaults[0] if defaults else audio[0]
    title = selected.get("title") or ""
    code = language(selected.get("language"))
    title_chinese = bool(
        re.search(r"中文|普通话|普通話|国语|國語|粤语|粵語|mandarin|cantonese", title, re.I)
    )
    if (
        selected.get("comment")
        or COMMENTARY.search(title)
        or (code and code != "zh" and title_chinese)
        or (code == "zh" and re.search(r"\bEnglish\b|英语|英語", title, re.I))
    ):
        raise ProviderError("default_audio_ambiguous")
    if code is None and title_chinese:
        code = "zh"
    if code is None:
        raise ProviderError("default_audio_unknown")
    return {
        "index": selected["index"],
        "language": code,
        "selection": "explicit_default" if defaults else "first_audio_inferred",
    }


def simplified_track(stream):
    tag = (stream.get("language") or "").lower().replace("_", "-")
    title = stream.get("title") or ""
    if (
        stream.get("forced")
        or stream.get("comment")
        or COMMENTARY.search(title)
        or re.search(r"forced|强制|強制", title, re.I)
    ):
        return False
    hans = tag in {"zh-hans", "chs"} or bool(
        re.search(r"简体|簡體|simplified|\bchs\b", title, re.I)
    )
    hant = tag in {"zh-hant", "cht"} or bool(
        re.search(r"繁体|繁體|traditional|\bcht\b", title, re.I)
    )
    if hans and (hant or (language(tag) not in {None, "zh"})):
        raise ProviderError("embedded_subtitle_ambiguous")
    return hans and not hant


def decode(data):
    if not data or len(data) > MAX_SUBTITLE:
        raise ProviderError("subtitle_size_invalid")
    codecs = ["utf-16"] if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030"]
    text = None
    for encoding in codecs:
        try:
            text = data.decode(encoding)
            break
        except UnicodeError:
            continue
    if text is None:
        best = from_bytes(data).best()
        if best is not None and best.percent_chaos <= 10:
            text = str(best)
    if text is None or "\x00" in text or re.search(r"<(?:!doctype\s+html|html|body)\b", text, re.I):
        raise ProviderError("subtitle_not_text")
    return text


def validate_subtitle(data, extension, duration, *, delay_ms=0, full=False):
    extension = extension.lower().lstrip(".")
    if extension not in {"srt", "ass", "ssa"}:
        raise ProviderError("subtitle_format_unsupported")
    text = decode(data)
    # pysubs2 can silently skip malformed rows: check all declared timings first.
    if extension == "srt":
        timings = re.findall(r"(?m)^([^\n]*-->[^\n]*)$", text.replace("\r", ""))
        if not timings or any(
            not re.fullmatch(
                r"\s*\d{1,3}:[0-5]\d:[0-5]\d[,\.]\d{3}\s*-->\s*\d{1,3}:[0-5]\d:[0-5]\d[,\.]\d{3}\s*",
                row,
            )
            for row in timings
        ):
            raise ProviderError("subtitle_invalid_timing")
    elif "[Events]" not in text or "[Script Info]" not in text:
        raise ProviderError("subtitle_invalid_format")
    try:
        subs = pysubs2.SSAFile.from_string(text, format_=extension)
    except Exception:
        raise ProviderError("subtitle_invalid_format") from None
    cues = [c for c in subs if not c.is_comment and c.plaintext.strip()]
    if not cues or (extension == "srt" and len(cues) != len(timings)):
        raise ProviderError("subtitle_no_dialogue")
    if not isinstance(delay_ms, int) or abs(delay_ms) > 600000:
        raise ProviderError("subtitle_delay_invalid")
    if any(c.start < 0 or c.end <= c.start for c in cues):
        raise ProviderError("subtitle_invalid_timing")
    # Negative Shooter delays legitimately trim pre-roll. Drop cues wholly before
    # zero and clip crossing cues; never leave negative timestamps in the export.
    subs.shift(ms=delay_ms)
    dropped = sum(c.end <= 0 for c in subs)
    subs.events = [c for c in subs if c.end > 0]
    for cue in subs:
        cue.start = max(0, cue.start)
    cues = [c for c in subs if not c.is_comment and c.plaintext.strip()]
    if not cues:
        raise ProviderError("subtitle_no_dialogue")
    end = max(c.end for c in cues)
    start = min(c.start for c in cues)
    if not duration or not math.isfinite(duration) or duration <= 0:
        raise ProviderError("subtitle_duration_unknown")
    if end > (duration + max(10, duration * 0.1)) * 1000:
        raise ProviderError("subtitle_timeline_mismatch")
    # Explicit Hans labels alone do not prove a full subtitle. Require parsed
    # dialogue spanning a substantial part of the film, within the read budget.
    if full and (
        end < duration * 500 or start > duration * 200 or (duration > 600 and len(cues) < 20)
    ):
        raise ProviderError("embedded_subtitle_incomplete")
    if full:
        dialogue = "\n".join(c.plaintext for c in cues)
        if (
            not re.search(r"[\u3400-\u9fff]", dialogue)
            or TO_SIMPLIFIED.convert(dialogue) != dialogue
        ):
            raise ProviderError("embedded_subtitle_not_simplified")
    body = subs.to_string(extension).encode("utf-8")
    evidence = {
        "format": extension,
        "cues": len(cues),
        "start_ms": start,
        "end_ms": end,
        "delay_ms": delay_ms,
        "trimmed_preroll_cues": dropped,
        "sync": "coarse_timeline_only",
    }
    return body, evidence


def matches_external(path, media_path):
    item, media = PurePosixPath(path), PurePosixPath(media_path)
    if item.suffix.lower() not in {".srt", ".ass", ".ssa", ".idx", ".sub"}:
        return False
    if item.parent not in {media.parent, media.parent / "subs", media.parent / "subtitles"}:
        return False
    stem, video = item.stem.casefold(), media.stem.casefold()
    if stem == video:
        return True
    if not stem.startswith(video + "."):
        return False
    # Only language/track suffixes; a different movie sharing a prefix is not a match.
    suffix = stem[len(video) + 1 :]
    if re.fullmatch(r"shooter\.[0-9a-f]{12}", suffix):
        return True
    suffixes = suffix.split(".")
    return all(
        re.fullmatch(r"[a-z]{2,3}(?:-(?:hans|hant|[a-z]{2}))?|sdh|cc|\d+", s) for s in suffixes
    )


async def fingerprint(storage, media, check=lambda: None):
    size = media.size
    if not size or size < 12288:
        raise ProviderError("shooter_media_too_small")
    hashes = []
    for offset in (4096, size * 2 // 3, size // 3, size - 8192):
        check()
        block = await storage.read_range(media.remote_path, offset, 4096, size)
        if len(block) != 4096:
            raise ProviderError("storage_short_read")
        hashes.append(hashlib.md5(block, usedforsecurity=False).hexdigest())
    return ";".join(hashes)
