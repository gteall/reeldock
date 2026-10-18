"""Names of media sidecars; video paths are never metadata deletion targets."""

import re
from pathlib import PurePosixPath

from reeldock.subtitles import matches_external

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CONTROL = re.compile(r"(?:manifest-v[0-9]+-[0-9a-f]{12}|embedded-[0-9a-f]{16}-[0-9a-f]{12})\.json")


def control_file(path):
    value = PurePosixPath(path)
    return value.parent == PurePosixPath(".reeldock") and bool(CONTROL.fullmatch(value.name))


def image_alias(path, kind, entries, media_path=None):
    original = PurePosixPath(path)
    if kind == "nfo":
        return next(
            (e.path for e in entries if not e.is_dir and e.path.casefold() == path.casefold()), path
        )
    stems = [original.stem]
    if kind == "poster":
        stems += ["movie-poster", "tvshow-poster", "folder"]
    if media_path and kind in {"poster", "fanart"}:
        stems.append(PurePosixPath(media_path).stem + "-" + kind)
    names = {
        str(PurePosixPath(entry.path)).casefold(): entry.path
        for entry in entries
        if not entry.is_dir and PurePosixPath(entry.path).suffix.lower() in IMAGE_EXTENSIONS
    }
    for stem in stems:
        for extension in [original.suffix, ".jpg", ".jpeg", ".png", ".webp"]:
            candidate = original.parent / (stem + extension)
            found = names.get(str(candidate).casefold())
            if found:
                return found
    return path


def metadata_file(relative, media_paths, *, kind="movie"):
    path = PurePosixPath(relative)
    if control_file(relative):
        return True
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        if path.parent == PurePosixPath(".actors"):
            return True
        if path.parent == PurePosixPath(".") and (
            path.stem.lower() in {"poster", "fanart", "folder", "movie-poster", "tvshow-poster"}
            or (kind == "tv" and re.fullmatch(r"season(?:\d+|specials)-poster", path.stem, re.I))
        ):
            return True
    if kind == "tv" and relative.casefold() == "tvshow.nfo":
        return True
    if kind == "movie" and relative.casefold() == "movie.nfo":
        return True
    for media in map(PurePosixPath, media_paths):
        if str(path).casefold() == str(media.with_suffix(".nfo")).casefold() or matches_external(
            str(path), str(media)
        ):
            return True
        if path.parent == media.parent and path.suffix.lower() in IMAGE_EXTENSIONS:
            if path.stem.casefold() in {
                media.stem.casefold() + suffix for suffix in ("-poster", "-fanart", "-thumb")
            }:
                return True
    return False
