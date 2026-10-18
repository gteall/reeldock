"""Kodi export contains TMDB facts only; no guessed streamdetails or audio data."""

import io
import re
import warnings
from xml.etree import ElementTree as XML

from defusedxml import ElementTree as SafeXML
from PIL import Image, UnidentifiedImageError

from reeldock.domain import ProviderError

MAX_ASSET_BYTES = 20 * 1024 * 1024
MAX_NFO_BYTES = 2 * 1024 * 1024


def validate_image(body: bytes, *, convert=False) -> bytes:
    if not body or len(body) > MAX_ASSET_BYTES:
        raise ProviderError("invalid_image")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(body)) as probe:
                if (
                    probe.format not in {"JPEG", "PNG", "WEBP"}
                    or probe.width * probe.height > 40_000_000
                ):
                    raise ValueError()
                probe.verify()
            with Image.open(io.BytesIO(body)) as picture:
                picture.load()
                if not convert:
                    return body
                output = io.BytesIO()
                picture.convert("RGB").save(output, "JPEG", quality=92)
                return output.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        raise ProviderError("invalid_image") from None


def read_nfo(body: bytes, tag="movie"):
    if len(body) > MAX_NFO_BYTES:
        raise ProviderError("invalid_nfo")
    try:
        root = SafeXML.fromstring(body)
        if root.tag != tag or not root.findtext("title"):
            raise ValueError()
        return root
    except Exception:
        raise ProviderError("invalid_nfo") from None


def nfo_ids(body: bytes, tag="movie") -> set[str]:
    root = read_nfo(body, tag)
    ids = {
        node.text.strip()
        for node in root.findall("uniqueid")
        if node.get("type") == "tmdb" and node.text and node.text.strip()
    }
    legacy = root.findtext("tmdbid")
    if legacy:
        ids.add(legacy.strip())
    if any(not re.fullmatch(r"[1-9]\d{0,9}", value) for value in ids):
        raise ProviderError("invalid_nfo_id")
    return ids


def imdb_ids(body: bytes, tag="movie") -> set[str]:
    root = read_nfo(body, tag)
    values = [n.text for n in root.findall("uniqueid") if n.get("type") == "imdb"]
    values += [root.findtext("imdbid"), root.findtext("id")]
    return {v.strip() for v in values if v and re.fullmatch(r"tt\d{5,12}", v.strip())}


def actor_filename(name: str) -> str:
    # Kodi resolves actor names with spaces replaced by underscores. Refuse ambiguous
    # illegal names rather than inventing a suffix Kodi cannot resolve.
    if not name or len(name.encode("utf-8")) > 220 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ProviderError("actor_filename_unsafe")
    if name in {".", ".."} or name.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }:
        raise ProviderError("actor_filename_unsafe")
    return name.replace(" ", "_") + ".jpg"


def export_nfo(movie, actors, *, tag="movie", showtitle=None):
    root = XML.Element(tag)

    def add(parent, name, value, **attributes):
        if value is not None and value != "":
            XML.SubElement(parent, name, attributes).text = str(value)

    add(root, "title", movie.title)
    add(root, "originaltitle", movie.original_title)
    add(root, "year", movie.year)
    add(root, "plot", movie.overview)
    add(root, "tagline", movie.tagline)
    add(root, "premiered", movie.release_date)
    add(root, "originallanguage", movie.original_language)
    add(root, "uniqueid", movie.external_id, type="tmdb", default="true")
    add(root, "uniqueid", movie.imdb_id, type="imdb")
    if tag == "tvshow":
        import json

        add(root, "episodeguide", json.dumps({"tmdb": movie.external_id}))
    if tag == "episodedetails":
        add(root, "showtitle", showtitle)
        add(root, "season", movie.season)
        add(root, "episode", movie.episode)
        add(root, "aired", movie.release_date)
    if movie.rating is not None:
        ratings = XML.SubElement(root, "ratings")
        rating = XML.SubElement(ratings, "rating", {"name": "tmdb", "max": "10", "default": "true"})
        add(rating, "value", movie.rating)
        add(rating, "votes", movie.votes)
    for genre in movie.genres:
        add(root, "genre", genre)
    for name in movie.directors:
        add(root, "director", name)
    for name in movie.writers:
        add(root, "credits", name)
    for person in actors:
        actor = XML.SubElement(root, "actor")
        add(actor, "name", person.name)
        add(actor, "role", person.role)
        add(actor, "order", person.order)
        # No remote thumb or unverified relative thumb; Kodi uses .actors convention.
    root.append(XML.Comment(" Generated by ReelDock; metadata source: TMDB; no media probe "))
    XML.indent(root)
    body = XML.tostring(root, encoding="utf-8", xml_declaration=True)
    read_nfo(body, tag)
    return body
