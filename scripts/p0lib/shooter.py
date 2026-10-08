from __future__ import annotations

import json
import re
import urllib.parse

from .core import CheckError, Transport, origin


PUBLIC_HASH = "234b0ff3685d6c46164b6b48cd39d69f;8be57624909f9d365dc81df43399d496;436de72e3c36a05a07875cc3249ae31a;237f498cfee89c67a22564e61047b053"


def validate_subtitle(data: bytes, extension: str):
    if not data or len(data) > 2 * 1024 * 1024:
        raise CheckError("subtitle_size_invalid")
    text = None
    encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            break
        except UnicodeError:
            continue
    if text is None or "\x00" in text or re.search(r"<(?:!doctype\s+html|html|body)\b", text, re.I):
        raise CheckError("subtitle_not_text")
    extension = extension.lower().lstrip(".")
    cues = []
    if extension == "srt":
        pattern = r"(?m)^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{3})[^\n]*\n([^\n]+)"
        cues = [(a, b, content.strip()) for a, b, content in re.findall(pattern, text)]
    elif extension in {"ass", "ssa"}:
        if "[Script Info]" not in text or "[Events]" not in text:
            raise CheckError("invalid_ass_sections")
        in_events, fields = False, []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("["):
                in_events = line.lower() == "[events]"
            if not in_events:
                continue
            if line.lower().startswith("format:"):
                fields = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
            elif line.lower().startswith("dialogue:"):
                if not all(f in fields for f in ("start", "end", "text")):
                    raise CheckError("invalid_ass_format")
                values = line.split(":", 1)[1].split(",", len(fields) - 1)
                if len(values) != len(fields):
                    raise CheckError("invalid_ass_dialogue")
                row = dict(zip(fields, values))
                cues.append((row["start"].strip(), row["end"].strip(), row["text"].strip()))
    else:
        raise CheckError("unsupported_subtitle_format")
    if not cues:
        raise CheckError("subtitle_no_dialogue")
    def seconds(value):
        m = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{2,3})", value)
        if not m:
            raise CheckError("invalid_subtitle_timestamp")
        h, minute, sec, frac = m.groups()
        if int(minute) > 59 or int(sec) > 59:
            raise CheckError("invalid_subtitle_timestamp")
        return int(h) * 3600 + int(minute) * 60 + int(sec) + int(frac) / 10 ** len(frac)
    for start, end, content in cues:
        if seconds(end) <= seconds(start) or not content:
            raise CheckError("invalid_subtitle_cue")
    return {"format": extension, "dialogue_cues": len(cues), "bytes": len(data),
            "language_and_video_sync": "unverified"}


def check_shooter(transport: Transport, endpoint: str, filehash: str, filename: str):
    if not re.fullmatch(r"[0-9a-fA-F]{32}(;[0-9a-fA-F]{32}){3}", filehash):
        raise CheckError("invalid_shooter_hash")
    base_origin = origin(endpoint)
    body = urllib.parse.urlencode({"filehash": filehash, "pathinfo": filename,
                                   "format": "json", "lang": "Chn"}).encode()
    response = transport.request("POST", endpoint, body=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    if response.body.strip() in {b"\xff", b"-1", b"[]"}:
        raise CheckError("subtitle_no_match")
    try:
        candidates = json.loads(response.body)
    except (ValueError, UnicodeError):
        raise CheckError("invalid_shooter_json") from None
    if not isinstance(candidates, list) or not candidates:
        raise CheckError("invalid_shooter_candidates")
    failures = []
    for candidate in candidates[:10]:
        if not isinstance(candidate, dict):
            raise CheckError("invalid_shooter_candidates")
        files = candidate.get("Files", candidate.get("files", []))
        if not isinstance(files, list):
            raise CheckError("invalid_shooter_candidates")
        for file in files[:10]:
            try:
                if not isinstance(file, dict):
                    raise CheckError("invalid_shooter_file")
                extension = file.get("Ext", file.get("ext", ""))
                link = file.get("Link", file.get("link", ""))
                if not isinstance(extension, str) or not isinstance(link, str):
                    raise CheckError("invalid_shooter_file")
                target_origin = origin(link)
                if target_origin != base_origin and target_origin[1] not in transport.redirect_hosts:
                    raise CheckError("subtitle_host_not_allowed")
                if base_origin[0] == "https" and target_origin[0] != "https":
                    raise CheckError("subtitle_downgrade_blocked")
                content = transport.request("GET", link, max_bytes=2 * 1024 * 1024).body
                result = validate_subtitle(content, extension)
                delay = candidate.get("Delay", candidate.get("delay", 0))
                if not isinstance(delay, (int, float)) or isinstance(delay, bool):
                    raise CheckError("invalid_shooter_delay")
                return {"candidates": len(candidates), "download": result,
                        "delay_ms": delay, "public_fixture": filehash == PUBLIC_HASH}
            except CheckError as exc:
                failures.append(exc.code)
    raise CheckError("no_valid_subtitle_download", candidate_failure_codes=sorted(set(failures)))
