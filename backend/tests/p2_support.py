import io
from collections import Counter
from pathlib import PurePosixPath

from PIL import Image

from reeldock.domain import Artwork, Movie, Person, ProviderError, RemoteEntry


def jpeg(color="navy"):
    stream = io.BytesIO()
    Image.new("RGB", (40, 60), color).save(stream, "JPEG")
    return stream.getvalue()


class MemoryDAV:
    def __init__(self):
        self.directories = {"/incoming", "/library", "/incoming/Example (2020)"}
        self.media = {"/incoming/Example (2020)/Example.2020.1080p.mkv": 1024**3}
        self.files = {}
        self.calls = Counter()
        self.corrupt = set()
        self.timeout_after_put = set()

    def __call__(self, config):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def list(self, path):
        self.calls["list"] += 1
        if path not in self.directories:
            raise ProviderError("storage_not_found")
        paths = self.directories | self.files.keys() | self.media.keys()
        return [self.entry(p) for p in sorted(paths) if str(PurePosixPath(p).parent) == path]

    def entry(self, path):
        if path in self.directories:
            return RemoteEntry(path=path, is_dir=True)
        if path in self.files:
            content = self.files[path]
            from reeldock.cache import sha256

            return RemoteEntry(path=path, is_dir=False, size=len(content), etag=sha256(content))
        if path in self.media:
            return RemoteEntry(
                path=path, is_dir=False, size=self.media[path], etag="video-version-1"
            )
        raise ProviderError("storage_not_found")

    async def stat(self, path):
        self.calls["stat"] += 1
        return self.entry(path)

    async def read_small(self, path, max_bytes):
        assert PurePosixPath(path).suffix.lower() in {".nfo", ".jpg"}, "P2 forbidden content read"
        self.calls["get:" + PurePosixPath(path).name] += 1
        if path not in self.files:
            raise ProviderError("storage_not_found")
        if len(self.files[path]) > max_bytes:
            raise ProviderError("storage_byte_budget")
        return self.files[path]

    async def put(self, path, content):
        assert PurePosixPath(path).suffix.lower() in {".nfo", ".jpg"}
        self.calls["put:" + PurePosixPath(path).name] += 1
        if path in self.files:
            raise ProviderError("storage_conflict")
        self.files[path] = b"corrupt" if PurePosixPath(path).name in self.corrupt else content
        if PurePosixPath(path).name in self.timeout_after_put:
            raise ProviderError("storage_timeout", retryable=True)

    async def mkdir(self, path):
        self.directories.add(path)

    async def read_range(self, *args):
        self.calls["media_reads"] += 1
        raise AssertionError("P2 forbidden video read")

    async def move(self, *args):
        self.calls["move"] += 1
        raise AssertionError("P2 forbidden MOVE")


class FakeTMDB:
    def __init__(self):
        self.calls = Counter()
        self.bad = set()
        self.movies = [
            Movie(
                source="tmdb",
                external_id="123",
                title="Example",
                original_title="Example",
                original_language="en",
                year=2020,
                overview="中文 & plot",
            )
        ]
        self.people = [
            Person(
                source="tmdb",
                external_id="1",
                name="Actor One",
                role="Hero",
                profile=Artwork(source="tmdb", external_id="/actor.jpg", kind="actor"),
            ),
            Person(source="tmdb", external_id="2", name="No Image"),
        ]

    def __call__(self, config, cache):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def search(self, *args):
        self.calls["search"] += 1
        return self.movies

    async def movie_details(self, identifier):
        self.calls["details"] += 1
        for movie in self.movies:
            if movie.external_id == identifier:
                return movie
        raise ProviderError("tmdb_not_found")

    async def credits(self, *args):
        self.calls["credits"] += 1
        return self.people

    async def images(self, *args):
        self.calls["images"] += 1
        return [
            Artwork(source="tmdb", external_id="/" + kind + ".jpg", kind=kind)
            for kind in ["poster", "fanart"]
        ]

    async def download_artwork(self, art):
        self.calls["download:" + art.kind] += 1
        return b"<html>not an image</html>" if art.kind in self.bad else jpeg()
