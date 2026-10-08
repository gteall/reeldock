from __future__ import annotations

import json
import shutil
import struct
import subprocess
import uuid
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

from .core import CheckError


def png_bytes(width, height, rgb):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = (b"\0" + bytes(rgb) * width) * height
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def create_kodi_fixture(output: Path, media: Path, ffmpeg="ffmpeg"):
    executable = shutil.which(ffmpeg)
    if not executable:
        raise CheckError("ffmpeg_missing")
    run_id = uuid.uuid4().hex[:8]
    actor_name = "ReelDock Test Actor " + run_id
    directory = output / f"ReelDock P0 Test {run_id} (2026)"
    directory.mkdir(parents=True, exist_ok=True)
    video = directory / ("ReelDock P0 Test (2026)" + media.suffix)
    if not video.exists():
        shutil.copyfile(media, video)
    movie = ET.Element("movie")
    for tag, text in (("title", "ReelDock P0 Test"), ("originaltitle", "ReelDock P0 Test"),
                      ("year", "2026"), ("plot", "Generated synthetic compatibility test; no real film metadata.")):
        ET.SubElement(movie, tag).text = text
    actor = ET.SubElement(movie, "actor")
    ET.SubElement(actor, "name").text = actor_name
    ET.SubElement(actor, "role").text = "Synthetic test actor"
    ET.SubElement(actor, "order").text = "0"
    ET.ElementTree(movie).write(video.with_suffix(".nfo"), encoding="utf-8", xml_declaration=True)
    actors = directory / ".actors"
    actors.mkdir(exist_ok=True)
    # JPEG follows Kodi's documented .actors convention; no external artwork dependencies.
    for target, width, height, rgb in (
        (actors / (actor_name.replace(" ", "_") + ".jpg"), 100, 150, (40, 180, 100)),
        (directory / "poster.jpg", 100, 150, (50, 100, 180)),
        (directory / "fanart.jpg", 320, 180, (90, 60, 140)),
    ):
        try:
            result = subprocess.run([executable, "-nostdin", "-v", "error", "-n", "-i", "pipe:0",
                                     "-frames:v", "1", str(target)], input=png_bytes(width, height, rgb),
                                    capture_output=True, timeout=15, check=False)
        except subprocess.TimeoutExpired:
            raise CheckError("artwork_generation_timeout") from None
        if result.returncode or not target.read_bytes().startswith(b"\xff\xd8\xff"):
            raise CheckError("artwork_generation_failed")
    (output / "kodi-observation.example.json").write_text(json.dumps({
        "kodi_version": "", "first_import_nfo": None, "local_artwork": None,
        "actor_image": None, "refresh_local_info": None, "offline_no_cached_actor_image": None,
        "notes": "Record actual observations; generated fixtures alone do not pass Kodi acceptance."
    }, indent=2) + "\n")
    return {"synthetic_video": True, "nfo_streamdetails": False, "remote_artwork_urls": False,
            "actor_format": "jpg", "kodi_import": "unverified", "fixture_id": run_id}


def check_kodi_observation(path: Path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise CheckError("invalid_kodi_observation") from None
    required = ("first_import_nfo", "local_artwork", "actor_image", "refresh_local_info",
                "offline_no_cached_actor_image")
    if not isinstance(data, dict) or not isinstance(data.get("kodi_version"), str) or not data["kodi_version"].strip():
        raise CheckError("kodi_version_required")
    observations = []
    for key in required:
        value = data.get(key)
        if value is not None and value is not True and value is not False:
            raise CheckError("invalid_kodi_observation_value")
        observations.append({"check": key, "status": "unverified" if value is None else ("passed" if value else "failed")})
    # Do not include operator notes/private library paths in machine-readable results.
    return {"observations": observations, "evidence_type": "operator_attestation"}
