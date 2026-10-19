import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import PurePosixPath

from reeldock.domain import ProviderError

ID_PATTERN = re.compile(r"(?i)[\[{](?:tmdb(?:id)?)[-:= ]+(\d+)[\]}]")
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".m2ts", ".ts", ".wmv", ".webm"}
TEMP_PATTERN = re.compile(r"(?i)(\.part|\.tmp|\.!qb|\.crdownload|\.download|\.aria2)$")
EPISODE_PATTERN = re.compile(r"(?i)(?:\bS\d{1,2}E\d{1,3}\b|\b\d{1,2}x\d{2}\b|第.{1,5}[季集])")
TECH_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9])(?:2160p|1080[pi]|720p|4k|bluray|b[dr]rip|web[- .]?dl|"
    r"webrip|hdtv|[xh][ ._-]?26[45]|hevc|remux|uhd|hdr10\+?|dv|atmos|"
    r"ddp?\d(?:\.\d)?|aac|dts(?:[- ]?hd)?|dolby[ ._-]?vision)(?![a-z0-9])"
)
BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】")
NOISE = re.compile(
    r"(?i)^(?:杜比视界(?:版(?:本)?)?|杜比全景声|简[繁体中英]+(?:双语)?(?:字幕)?|"
    r"繁体(?:字幕)?|双语字幕|内封字幕|内嵌字幕|国语(?:配音)?|中字|无字幕|"
    r"全集|完结|全\s*\d+\s*集|\d+\s*(?:全集|集全|集)|"
    r"\d+(?:\.\d+)?\s*(?:gib|gb|tb|g)|"
    r"(?:2160p|1080p|720p|4k|hdr10\+?|hdr|dv|web[- .]?dl|bluray|"
    r"h[ .]?26[45]|x26[45]|hevc|ddp?[ .\d]*|atmos|aac|dts)[\w .+\-]*)$"
)
SEASON_PATTERN = re.compile(
    r"(?i)(?:第?[0-9零〇一二两三四五六七八九十百]+"
    r"(?:\s*[-~～至到]\s*[0-9零〇一二两三四五六七八九十百]+)?季|"
    r"(?<![a-z0-9])(?:seasons?[ ._-]*|s)\d{1,2}"
    r"(?:[ ._-]*e\d{1,3})?(?!\d)|(?<!\d)\d{1,2}x\d{1,3}(?!\d))"
)
GENERIC_TITLES = {"电影", "电视剧", "剧集", "影视", "movies", "tv", "tvshows", "incoming"}
MATCH_VERSION = 1
MAX_SEARCHES = 5


@dataclass
class ParsedName:
    title: str
    year: int | None
    explicit_id: str | None
    season: int | None = None


@dataclass
class SearchQuery:
    parsed: ParsedName
    source: str
    weak: bool = False


def explicit_ids(value: str) -> set[str]:
    return set(ID_PATTERN.findall(value))


def parse_name(filename: str, *, kind="movie") -> ParsedName:
    value = PurePosixPath(filename).name
    if PurePosixPath(value).suffix.lower() in VIDEO_EXTENSIONS:
        value = str(PurePosixPath(value).with_suffix(""))
    ids = explicit_ids(value)
    if len(ids) > 1:
        raise ProviderError("tmdb_id_conflict")
    value = ID_PATTERN.sub("", value)
    value = unicodedata.normalize("NFKC", value)
    value = BRACKETS.sub(lambda m: " " if NOISE.fullmatch(m[0][1:-1].strip()) else m[0], value)
    value = TECH_PATTERN.split(value, maxsplit=1)[0]
    release_year = None
    for year in re.finditer(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value):
        # Preserve numeric titles and years embedded in an actual title.
        prefix = value[: year.start()].strip(" ._-[]()【】")
        suffix = value[year.end() :].strip(" ._-[]()【】")
        if prefix and not suffix and int(year[1]) <= datetime.now().year + 2:
            release_year, value = int(year[1]), value[: year.start()]
            break
    season = None
    if kind == "tv" and (marker := SEASON_PATTERN.search(value)):
        from reeldock.episodes import number

        digits = re.search(r"[0-9零〇一二两三四五六七八九十百]+", marker[0])
        season = number(digits[0]) if digits else None
        value = value[: marker.start()]
        if release_year is None:
            year = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)[ ._()\[\]【】-]*$", value)
            if (
                year
                and value[: year.start()].strip(" ._-[]()【】")
                and int(year[1]) <= datetime.now().year + 2
            ):
                release_year, value = int(year[1]), value[: year.start()]
    value = re.sub(r"[._]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" -")
    value = re.sub(r"[\s(\[【]+$", "", value)
    return ParsedName(value, release_year, next(iter(ids), None), season)


def normalized(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def rank_candidates(parsed: ParsedName, movies, *, kind="movie", weak=False) -> list[dict]:
    rows = []
    for movie in movies:
        titles = [t for t in [movie.title, movie.original_title] if normalized(t)]
        if not titles:
            continue
        similarity = max(
            SequenceMatcher(None, normalized(parsed.title), normalized(t)).ratio()
            for t in titles
            if t
        )
        exact = any(normalized(parsed.title) == normalized(t) for t in titles if t)
        year_match = parsed.year is not None and movie.year == parsed.year
        if year_match:
            score = similarity * 0.75 + 0.25
        else:
            score = similarity * 0.9
        reason = "title_exact" if exact else "title_similar"
        if parsed.year and movie.year and movie.year != parsed.year:
            # A season release year need not equal the series' first-air year.
            reason = "season_year_differs" if kind == "tv" else "year_conflict"
            if kind == "tv":
                score = 0.9 if exact else min(score, 0.8)
            else:
                score = min(score, 0.65)
        if weak:
            score, reason = min(score, 0.8), "partial_title"
        if not parsed.title:
            score = min(score, 0.65)
        rows.append(
            {
                "id": movie.external_id,
                "title": movie.title,
                "original_title": movie.original_title,
                "year": movie.year,
                "confidence": round(score, 3),
                "exact": exact,
                "reason": reason,
            }
        )
    return sorted(rows, key=lambda r: (-r["confidence"], r["id"]))


def search_queries(package_path, media_paths, kind, *, root=None, override=None):
    """Generate bounded full-title queries before conservative partial fallbacks."""
    if override:
        return [SearchQuery(ParsedName(override, None, None), "manual")]
    paths = sorted(media_paths)
    directory = parse_name(PurePosixPath(package_path).name, kind=kind)
    files = [parse_name(PurePosixPath(path).name, kind=kind) for path in paths]
    counts = Counter(normalized(item.title) for item in files if item.title)
    files.sort(key=lambda item: -counts[normalized(item.title)])
    sources = []
    if kind == "movie":
        sources.extend((item, "filename") for item in files[:1])
    sources.append((directory, "directory"))
    if kind == "tv":
        sources.extend((item, "filename") for item in files[:3])
    parent = PurePosixPath(package_path).parent
    if root and str(parent) != root and parent.is_relative_to(root):
        sources.append((parse_name(parent.name, kind=kind), "parent"))
    queries, seen = [], set()

    def add(parsed, source, weak=False):
        key = normalized(parsed.title)
        if not key or key in GENERIC_TITLES or re.fullmatch(r"e\d+", key):
            return
        if key in seen:
            # The directory may provide the year omitted from an otherwise
            # identical filename. Keep that evidence when deduplicating queries.
            existing = next(q.parsed for q in queries if normalized(q.parsed.title) == key)
            existing.year = existing.year if existing.year is not None else parsed.year
            existing.season = existing.season if existing.season is not None else parsed.season
            return
        seen.add(key)
        queries.append(SearchQuery(parsed, source, weak))

    for parsed, source in sources:
        add(parsed, source)
    for parsed, _ in sources:
        # A subtitle separated by a dash is not necessarily a different work name.
        alternatives = re.split(r"\s*[/|｜]\s*|\s+-\s+", parsed.title)
        mixed_languages = any(re.search(r"[\u3400-\u9fff]", p) for p in alternatives) and any(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9 .:'-]+", p) for p in alternatives
        )
        if len(alternatives) > 1:
            for title in alternatives:
                add(
                    ParsedName(title.strip(), parsed.year, None, parsed.season),
                    "alternate" if mixed_languages else "partial",
                    not mixed_languages,
                )
        bilingual = re.fullmatch(
            r"([\u3400-\u9fff][\u3400-\u9fff\d ]*)\s+([A-Za-z].+)", parsed.title
        )
        bracket_alias = re.fullmatch(
            r"([\u3400-\u9fff][\u3400-\u9fff\d ]*)\s*[\[(]([A-Za-z][A-Za-z0-9 .:'-]+)[\])]",
            parsed.title,
        )
        for match in [bilingual, bracket_alias]:
            if match:
                for title in match.groups():
                    add(ParsedName(title.strip(), parsed.year, None, parsed.season), "alternate")
        # Unknown bracket content is only removed as a weak fallback. In particular,
        # retain titles such as 【我推的孩子】 and numeric sequel names in the first pass.
        title = BRACKETS.sub(" ", parsed.title)
        title = re.sub(r"\s+", " ", title).strip()
        if title != parsed.title:
            add(ParsedName(title, parsed.year, None, parsed.season), "partial", True)
    return queries[:MAX_SEARCHES]


async def search_candidates(metadata, queries, kind, *, choose, report, check):
    evidence = {
        "version": MATCH_VERSION,
        "queries": [],
        "outcome": "searching" if queries else "no_results",
    }
    if queries:
        primary = queries[0].parsed
        evidence.update(parsed_title=primary.title, year=primary.year, season=primary.season)
    candidates = {}
    report(evidence, [])
    for query in queries:
        check()
        attempt = {"query": query.parsed.title, "source": query.source, "weak": query.weak}
        evidence["queries"].append(attempt)
        report(evidence, list(candidates.values()))
        try:
            movies = await metadata.search(query.parsed.title, kind, query.parsed.year)
        except ProviderError as error:
            attempt["error_code"], evidence["outcome"] = error.code, "search_failed"
            report(evidence, list(candidates.values()))
            raise
        check()
        attempt["result_count"] = len(movies)
        for row in rank_candidates(query.parsed, movies, kind=kind, weak=query.weak):
            row["query"] = query.parsed.title
            if (
                row["id"] not in candidates
                or row["confidence"] > candidates[row["id"]]["confidence"]
            ):
                candidates[row["id"]] = row
        rows = sorted(candidates.values(), key=lambda row: (-row["confidence"], row["id"]))[:20]
        selected = choose(rows)
        evidence["outcome"] = "matched" if selected else "low_confidence" if rows else "no_results"
        evidence["selected_id"] = selected
        report(evidence, rows)
        if selected:
            return selected, rows
    return None, sorted(candidates.values(), key=lambda row: (-row["confidence"], row["id"]))[:20]


def automatic_candidate(rows: list[dict]) -> str | None:
    # rank_candidates puts the highest score first, including homonyms and near ties.
    if rows and rows[0]["confidence"] > 0.8:
        return rows[0]["id"]
    return None
