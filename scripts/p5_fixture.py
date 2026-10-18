"""Container-only generated fixtures: persistent Mock DAV / TMDB / Shooter, real probe.

Not an application mode or a deployment entry point. Mount explicitly for validation.
"""

import asyncio
import base64
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

sys.path.insert(0, "/validation/tests")
from p2_support import jpeg  # noqa: E402
from p3_support import DAV  # noqa: E402
from p4_support import TVMetadata  # noqa: E402

from reeldock.app import create_app  # noqa: E402
from reeldock.domain import Artwork, Movie, Series, SubtitleCandidate  # noqa: E402
from reeldock.runtime import Runtime  # noqa: E402
from reeldock.server import serve  # noqa: E402

ROOT = Path("/mock-remote")


class PersistentDAV(DAV):
    def __init__(self):
        super().__init__()
        ROOT.mkdir(exist_ok=True)
        video = ROOT / "generated.mkv"
        if not video.exists():
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-n",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x180:rate=12",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440",
                    "-t",
                    "2",
                    "-c:v",
                    "mpeg4",
                    "-q:v",
                    "4",
                    "-c:a",
                    "aac",
                    "-metadata:s:a:0",
                    "language=eng",
                    "-disposition:a:0",
                    "default",
                    str(video),
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
        self.video = video.read_bytes()
        assert len(self.video) > 16384
        self.state = ROOT / "state.json"
        if self.state.exists():
            state = json.loads(self.state.read_text())
            self.directories, self.media = set(state["directories"]), state["media"]
            self.files = {key: base64.b64decode(value) for key, value in state["files"].items()}
            self.calls, self.order = Counter(state["calls"]), state["order"]
        else:
            self.directories = {"/incoming", "/library"}
            self.media = {}
            for name in ["ChineseMovie", "BadMovie", "ChineseTV", "ForeignTV"]:
                folder = f"/incoming/{name} (2020)"
                paths = (
                    [f"{folder}/{name}.2020.mkv"]
                    if "Movie" in name
                    else [
                        f"{folder}/Season 01/{name}.S01E01.mkv",
                        f"{folder}/Season 01/{name}.S01E02.mkv",
                    ]
                )
                for path in paths:
                    self.media[path] = len(self.video)
                    self.directories.update(
                        str(parent) for parent in PurePosixPath(path).parents if str(parent) != "/"
                    )
            self.save()

    def save(self):
        state = {
            "directories": sorted(self.directories),
            "media": self.media,
            "files": {key: base64.b64encode(value).decode() for key, value in self.files.items()},
            "calls": dict(self.calls),
            "order": self.order,
        }
        temporary = ROOT / "state.tmp"
        temporary.write_text(json.dumps(state))
        os.replace(temporary, self.state)

    async def read_small(self, path, max_bytes):
        result = await super().read_small(path, max_bytes)
        self.save()
        return result

    async def put(self, path, content):
        await super().put(path, content)
        self.calls["put-group:" + PurePosixPath(path).parts[2]] += 1
        self.save()

    async def mkdir(self, path):
        await super().mkdir(path)
        self.save()

    async def read_range(self, path, start, length, size):
        assert path in self.media and size == len(self.video)
        assert 0 <= start < start + length <= size
        self.calls["media_reads"] += 1
        self.calls["video-group:" + PurePosixPath(path).parts[2]] += 1
        self.order.append("range:" + path)
        self.save()
        return self.video[start : start + length]

    async def move(self, source, target):
        self.calls["move"] += 1
        self.calls["move-group:" + PurePosixPath(source).name] += 1
        self.order.append("move:" + source)
        self.perform_move(source, target)
        self.save()
        flag = ROOT / "crash-move"
        if flag.exists() and flag.read_text() == PurePosixPath(source).name:
            # Remote completion precedes lost response / container SIGKILL.
            (ROOT / "move-finished").touch()
            await asyncio.Event().wait()


class Metadata(TVMetadata):
    def __init__(self, dav):
        super().__init__()
        self.dav = dav
        self.movies = [
            Movie(
                source="tmdb",
                external_id=identifier,
                title=name,
                original_language=language,
                year=2020,
            )
            for identifier, name, language in [
                ("100", "ChineseMovie", "zh"),
                ("200", "BadMovie", "en"),
            ]
        ]
        self.series_items = [
            Series(
                source="tmdb",
                external_id=identifier,
                title=name,
                original_language=language,
                year=2020,
            )
            for identifier, name, language in [
                ("300", "ChineseTV", "cn"),
                ("400", "ForeignTV", "en"),
            ]
        ]

    async def search(self, query, kind, year=None):
        return [
            value
            for value in (self.series_items if kind == "tv" else self.movies)
            if value.title == query
        ]

    async def movie_details(self, identifier):
        return next(value for value in self.movies if value.external_id == identifier)

    async def series_details(self, identifier):
        return next(value for value in self.series_items if value.external_id == identifier)

    async def images(self, identifier, kind="movie", *, season=None, episode=None):
        kinds = (
            ["thumb"]
            if episode is not None
            else ["season_poster"]
            if season is not None
            else ["poster", "fanart"]
        )
        return [
            Artwork(source="tmdb", external_id=f"/{identifier}/{name}.jpg", kind=name)
            for name in kinds
        ]

    async def download_artwork(self, art):
        if art.external_id == "/200/poster.jpg" and not (ROOT / "repair-image").exists():
            return b"<html>controlled bad image</html>"
        return jpeg({"actor": "green", "fanart": "purple", "thumb": "orange"}.get(art.kind, "navy"))


class Subtitles:
    def __init__(self, dav):
        self.dav = dav

    def __call__(self, config):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def search(self, fingerprint, episode_context=None):
        context = episode_context or {}
        self.dav.calls["shooter:" + str(context.get("episode"))] += 1
        self.dav.order.append("shooter")
        self.dav.save()
        if context.get("episode") == 2 and not (ROOT / "repair-subtitles").exists():
            return []
        return [
            SubtitleCandidate(
                source="shooter",
                external_id="generated",
                format="srt",
                description="generated test subtitle",
                download_url="https://shooter.cn/generated.srt",
            )
        ]

    async def download(self, candidate):
        self.dav.calls["subtitle_downloads"] += 1
        self.dav.save()
        return "1\n00:00:00,100 --> 00:00:01,900\n影坞原创测试对白\n\n".encode()


def main():
    dav = PersistentDAV()
    app = create_app(
        Runtime(),
        storage_factory=dav,
        metadata_factory=Metadata(dav),
        subtitle_factory=Subtitles(dav),
    )
    serve(app)


if __name__ == "__main__":
    main()
