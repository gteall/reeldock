"""Portable, immutable evidence for embedded subtitles, bound to one source file."""

import json
import time
from pathlib import PurePosixPath

from sqlalchemy import select

from reeldock.cache import sha256
from reeldock.domain import ProviderError
from reeldock.models import Asset, Media
from reeldock.subtitles import selected_embedded_stream

MAX_MARKER = 512 * 1024


def marker_prefix(relative):
    return "embedded-" + sha256(relative.encode())[:16] + "-"


class EmbeddedMarkers:
    def __init__(self, completion):
        self.completion = completion

    async def save(self, claim, storage, config, package, media, evidence, probe):
        pipeline = self.completion
        pipeline.check(claim)
        current = await storage.stat(media.remote_path)
        await pipeline.verify_media(claim, storage, media)
        relative = str(PurePosixPath(media.remote_path).relative_to(package.remote_path))
        evidence = {key: value for key, value in evidence.items() if key != "marker_path"}
        evidence.setdefault("verified_at", time.time())
        payload = {
            "schema": 1,
            "kind": "embedded_subtitle",
            "media": {
                "path": relative,
                "size": current.size,
                "etag": current.etag,
                "modified": current.modified,
            },
            "policy_version": config.policy_version,
            "evidence": evidence,
            "probe": probe,
        }
        body = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if len(body) > MAX_MARKER:
            raise ProviderError("subtitle_marker_too_large")
        path = ".reeldock/" + marker_prefix(relative) + sha256(body)[:12] + ".json"
        await storage.mkdir(package.remote_path + "/.reeldock")
        await pipeline.save_asset(claim, storage, path, "subtitle_marker", body, media_id=media.id)
        with pipeline.db.sessions.begin() as session:
            pipeline.queue._owned(session, claim)
            for old in session.scalars(
                select(Asset).where(
                    Asset.media_id == media.id,
                    Asset.kind == "subtitle_marker",
                    Asset.relative_path != path,
                )
            ):
                old.required = False
        await pipeline.verify_media(claim, storage, media)
        return {**evidence, "marker_path": path}

    async def restore(self, claim, storage, config, package, media):
        pipeline = self.completion
        pipeline.check(claim)
        folder = package.remote_path + "/.reeldock"
        try:
            entries = await storage.list(folder)
        except ProviderError as error:
            if error.code == "storage_not_found":
                return None
            raise
        relative = str(PurePosixPath(media.remote_path).relative_to(package.remote_path))
        prefix = marker_prefix(relative)
        source = await storage.stat(media.remote_path)
        matches = []
        for entry in entries:
            name = PurePosixPath(entry.path).name
            if (
                entry.is_dir
                or not name.startswith(prefix)
                or not name.endswith(".json")
                or (entry.size or 0) > MAX_MARKER
            ):
                continue
            pipeline.check(claim)
            raw = await storage.read_small(entry.path, MAX_MARKER)
            try:
                data = json.loads(raw)
                saved = data["media"]
                identity = (
                    saved["path"] == relative
                    and saved["size"] == source.size
                    and (
                        saved.get("etag") == source.etag
                        if saved.get("etag")
                        else bool(saved.get("modified")) and saved["modified"] == source.modified
                    )
                )
                evidence = data["evidence"]
                if not (
                    identity
                    and data["schema"] == 1
                    and data["kind"] == "embedded_subtitle"
                    and data["policy_version"] == config.policy_version
                    and evidence["embedded"]["verification"]
                    in {"parsed_text", "stream_metadata", "manual_confirmation"}
                ):
                    continue
                selected_embedded_stream(
                    {"context_version": 1, "probe": data["probe"]}, 1, evidence["stream_index"]
                )
                if name != prefix + sha256(raw)[:12] + ".json":
                    continue
                matches.append((float(evidence["verified_at"]), data))
            except (ValueError, KeyError, TypeError, AttributeError, ProviderError):
                continue
        if not matches:
            return None
        _, data = max(matches, key=lambda item: item[0])
        await pipeline.verify_media(claim, storage, media)
        with pipeline.db.sessions.begin() as session:
            pipeline.queue._owned(session, claim)
            row = session.get(Media, media.id)
            row.probe_status = "probed"
            row.probe_evidence = {
                "context_version": claim.context_version,
                "probe": data["probe"],
                **{
                    key: data["evidence"][key]
                    for key in ("audio", "audio_error")
                    if key in data["evidence"]
                },
            }
        return pipeline.media_result(media.id, claim, "embedded_zh_hans", evidence=data["evidence"])
