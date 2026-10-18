import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import PurePosixPath

from reeldock.domain import ProviderError

ID_PATTERN = re.compile(r"(?i)[\[{](?:tmdb(?:id)?)[-:= ]+(\d+)[\]}]")
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".m2ts", ".ts", ".wmv", ".webm"}
TEMP_PATTERN = re.compile(r"(?i)(\.part|\.tmp|\.!qb|\.crdownload|\.download|\.aria2)$")
EPISODE_PATTERN = re.compile(r"(?i)(?:\bS\d{1,2}E\d{1,3}\b|\b\d{1,2}x\d{2}\b|第.{1,5}[季集])")


@dataclass
class ParsedName:
    title: str
    year: int | None
    explicit_id: str | None


def explicit_ids(value: str) -> set[str]:
    return set(ID_PATTERN.findall(value))


def parse_name(filename: str) -> ParsedName:
    value = PurePosixPath(filename).name
    if PurePosixPath(value).suffix.lower() in VIDEO_EXTENSIONS:
        value = str(PurePosixPath(value).with_suffix(""))
    ids = explicit_ids(value)
    if len(ids) > 1:
        raise ProviderError("tmdb_id_conflict")
    value = ID_PATTERN.sub("", value)
    year = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value)
    # A lone numeric title (e.g. 1917) is not a release year.
    prefix = value[: year.start()].strip(" ._-([（") if year else ""
    release_year = int(year[1]) if year and prefix else None
    if release_year:
        value = value[: year.start()]
    value = re.split(
        r"(?i)(?:\b(?:2160p|1080p|720p|4k|bluray|b[dr]rip|web[- .]?dl|"
        r"webrip|hdtv|x26[45]|h26[45]|hevc|remux)\b)",
        value,
    )[0]
    value = re.sub(r"[._]+", " ", value).strip(" -[]()（）")
    return ParsedName(value, release_year, next(iter(ids), None))


def normalized(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def rank_candidates(parsed: ParsedName, movies) -> list[dict]:
    rows = []
    for movie in movies:
        titles = [movie.title, movie.original_title]
        similarity = max(
            SequenceMatcher(None, normalized(parsed.title), normalized(t)).ratio()
            for t in titles
            if t
        )
        exact = any(normalized(parsed.title) == normalized(t) for t in titles if t)
        year_match = parsed.year is not None and movie.year == parsed.year
        score = round(similarity * 0.75 + (0.25 if year_match else 0), 3)
        if parsed.year and movie.year != parsed.year:
            score = min(score, 0.65)
        rows.append(
            {
                "id": movie.external_id,
                "title": movie.title,
                "original_title": movie.original_title,
                "year": movie.year,
                "confidence": score,
                "exact": exact and year_match,
            }
        )
    return sorted(rows, key=lambda r: (-r["confidence"], r["id"]))


def automatic_candidate(rows: list[dict]) -> str | None:
    # No year, conflicting year, homonyms and near ties require a human choice.
    if (
        rows
        and rows[0]["exact"]
        and (len(rows) == 1 or rows[0]["confidence"] - rows[1]["confidence"] >= 0.15)
    ):
        return rows[0]["id"]
    return None
