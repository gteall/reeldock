"""TMDB v3 only. Localized text never supplies the work's original language."""

import asyncio
import json
import random
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import SecretStr

from reeldock.domain import Artwork, Movie, Person, ProviderError

API = "https://api.themoviedb.org/3"
IMAGE = "https://image.tmdb.org/t/p/"


class TMDBProvider:
    def __init__(self, config, cache, *, transport=None, sleep=asyncio.sleep):
        self.config, self.cache, self.sleep = config, cache, sleep
        self.client = httpx.AsyncClient(
            timeout=config.http_timeout_seconds,
            proxy=config.tmdb_proxy_url or config.proxy_url,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )
        self.semaphore = asyncio.Semaphore(4)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    @staticmethod
    def external_id(value):
        if not re.fullmatch(r"[1-9]\d{0,9}", str(value)):
            raise ProviderError("tmdb_invalid_id")
        return str(value)

    async def request(self, path, parameters=None):
        parameters = parameters or {}
        cached = self.cache.response("tmdb:" + path, parameters)
        if cached is not None:
            return cached
        token = self.config.tmdb_token
        if not token or not token.get_secret_value():
            raise ProviderError("tmdb_credentials_required")
        params, headers = dict(parameters), {"Accept": "application/json"}
        value = token.get_secret_value().strip()
        if re.fullmatch(r"[0-9a-fA-F]{32}", value):
            params["api_key"] = value
        else:
            headers["Authorization"] = "Bearer " + value
        for attempt in range(3):
            try:
                async with (
                    self.semaphore,
                    self.client.stream(
                        "GET", API + path, params=params, headers=headers
                    ) as response,
                ):
                    status = response.status_code
                    if status == 429:
                        retry_after = response.headers.get("retry-after", "")
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            try:
                                wait = (
                                    parsedate_to_datetime(retry_after) - datetime.now(UTC)
                                ).total_seconds()
                            except (ValueError, TypeError):
                                wait = 2**attempt + random.random()
                        if wait > 15 or attempt == 2:
                            raise ProviderError("tmdb_rate_limited", retryable=True)
                    elif status != 200:
                        code = {
                            401: "tmdb_authentication_failed",
                            403: "tmdb_permission_denied",
                            404: "tmdb_not_found",
                        }.get(status, "tmdb_http_error")
                        raise ProviderError(code, retryable=status >= 500)
                    else:
                        body = await self.bounded(response, 8 * 1024 * 1024)
                        try:
                            result = json.loads(body)
                            if not isinstance(result, dict):
                                raise ValueError()
                        except (ValueError, UnicodeError):
                            raise ProviderError("tmdb_invalid_response") from None
                        self.cache.save_response("tmdb:" + path, parameters, result)
                        return result
                await self.sleep(max(0, wait))
            except httpx.TimeoutException:
                raise ProviderError("tmdb_timeout", retryable=True) from None
            except httpx.HTTPError:
                raise ProviderError("tmdb_network_error", retryable=True) from None
        raise ProviderError("tmdb_rate_limited", retryable=True)

    @staticmethod
    async def bounded(response, limit):
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=16384):
            body.extend(chunk)
            if len(body) > limit:
                raise ProviderError("tmdb_byte_budget")
        return bytes(body)

    @staticmethod
    def movie(value):
        release = value.get("release_date") or ""
        year = int(release[:4]) if re.fullmatch(r"\d{4}", release[:4]) else None
        return Movie(
            source="tmdb",
            external_id=str(value["id"]),
            title=value.get("title") or "",
            original_title=value.get("original_title") or "",
            imdb_id=value.get("imdb_id"),
            year=year,
            original_language=value.get("original_language"),
            overview=value.get("overview") or "",
            tagline=value.get("tagline") or "",
            release_date=release,
            genres=[g["name"] for g in value.get("genres", []) if g.get("name")],
            rating=value.get("vote_average"),
            votes=value.get("vote_count") or 0,
            poster_path=value.get("poster_path"),
            backdrop_path=value.get("backdrop_path"),
        )

    async def search(self, query, kind="movie", year=None):
        if kind != "movie":
            raise ProviderError("media_type_not_supported_p2")
        params = {"query": query, "language": "zh-CN", "include_adult": "false", "page": 1}
        # Include all years so remakes and incorrect years remain visible as candidates.
        body = await self.request("/search/movie", params)
        return [self.movie(value) for value in body.get("results", [])]

    async def find_movie(self, imdb_id):
        if not re.fullmatch(r"tt\d{5,12}", imdb_id):
            raise ProviderError("invalid_imdb_id")
        body = await self.request(
            "/find/" + imdb_id, {"external_source": "imdb_id", "language": "zh-CN"}
        )
        movies = body.get("movie_results") or []
        if len(movies) != 1:
            raise ProviderError("imdb_movie_match_needs_review")
        return self.movie(movies[0])

    async def movie_details(self, external_id):
        external_id = self.external_id(external_id)
        primary = await self.request("/movie/" + external_id, {"language": "zh-CN"})
        if str(primary.get("id")) != external_id or not primary.get("title"):
            raise ProviderError("tmdb_invalid_response")
        merged = dict(primary)
        # Never copy original_language out of a translated/fallback response.
        fields = ("title", "overview", "tagline", "genres")
        original = primary.get("original_language")
        languages = list(dict.fromkeys([original, "en-US"]))
        for language in languages:
            if not language or language in {"zh", "zh-CN"} or all(merged.get(f) for f in fields):
                continue
            fallback = await self.request("/movie/" + external_id, {"language": language})
            for field in fields:
                if not merged.get(field) and fallback.get(field):
                    merged[field] = fallback[field]
        movie = self.movie(merged)
        movie.original_language = original
        credits = await self.request("/movie/" + external_id + "/credits", {"language": "zh-CN"})
        movie.directors = [p["name"] for p in credits.get("crew", []) if p.get("job") == "Director"]
        movie.writers = list(
            dict.fromkeys(
                p["name"]
                for p in credits.get("crew", [])
                if p.get("department") == "Writing" and p.get("name")
            )
        )
        return movie

    async def configuration(self):
        body = await self.request("/configuration")
        images = body.get("images") or {}
        if images.get("secure_base_url") != IMAGE:
            raise ProviderError("tmdb_image_origin_rejected")
        return images

    async def artwork(self, path, kind, language=None):
        if not path:
            return None
        if not re.fullmatch(r"/[A-Za-z0-9_-]+\.(?:jpg|png|jpeg)", path):
            raise ProviderError("tmdb_image_path_rejected")
        config = await self.configuration()
        size, sizes = {
            "poster": ("w500", "poster_sizes"),
            "fanart": ("w1280", "backdrop_sizes"),
            "actor": ("w185", "profile_sizes"),
        }[kind]
        if size not in config.get(sizes, []):
            size = "original"
        if size not in config.get(sizes, []):
            raise ProviderError("tmdb_image_size_unavailable")
        return Artwork(
            source="tmdb",
            external_id=path,
            kind=kind,
            language=language,
            url=SecretStr(IMAGE + size + path),
        )

    async def credits(self, external_id, kind="movie"):
        if kind != "movie":
            raise ProviderError("media_type_not_supported_p2")
        body = await self.request(
            "/movie/" + self.external_id(external_id) + "/credits", {"language": "zh-CN"}
        )
        result = []
        for index, person in enumerate(body.get("cast", [])):
            result.append(
                Person(
                    source="tmdb",
                    external_id=str(person["id"]),
                    name=person["name"],
                    role=person.get("character") or "",
                    order=person.get("order", index),
                    profile=await self.artwork(person.get("profile_path"), "actor"),
                )
            )
        return result

    async def images(self, external_id, kind="movie"):
        if kind != "movie":
            raise ProviderError("media_type_not_supported_p2")
        body = await self.request("/movie/" + self.external_id(external_id) + "/images")
        details = await self.request(
            "/movie/" + self.external_id(external_id), {"language": "zh-CN"}
        )
        original = details.get("original_language")
        priority = list(dict.fromkeys(["zh", None, original, "en"]))
        result = []
        for key, asset_kind in [("posters", "poster"), ("backdrops", "fanart")]:
            rows = sorted(
                body.get(key, []),
                key=lambda r: (
                    priority.index(r.get("iso_639_1")) if r.get("iso_639_1") in priority else 9,
                    -(r.get("vote_average") or 0),
                    -(r.get("vote_count") or 0),
                ),
            )
            if not rows:
                fallback = details.get("poster_path" if asset_kind == "poster" else "backdrop_path")
                if fallback:
                    rows = [{"file_path": fallback}]
            for row in rows:
                art = await self.artwork(row.get("file_path"), asset_kind, row.get("iso_639_1"))
                if art:
                    result.append(art)
        return result

    async def download_artwork(self, artwork):
        if not artwork.url:
            artwork = await self.artwork(artwork.external_id, artwork.kind, artwork.language)
        if not artwork or not artwork.url:
            raise ProviderError("tmdb_image_missing")
        url = artwork.url.get_secret_value()
        if not url.startswith(IMAGE) or "?" in url:
            raise ProviderError("tmdb_image_origin_rejected")
        pointer = self.cache.response("tmdb:image", {"url": url})
        body = self.cache.get(pointer["key"]) if pointer else None
        if body is not None:
            return body
        try:
            async with self.client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise ProviderError("tmdb_image_download_failed", retryable=True)
                body = await self.bounded(response, 20 * 1024 * 1024)
        except httpx.HTTPError:
            raise ProviderError("tmdb_image_download_failed", retryable=True) from None
        # Validate before caching so a bad image does not poison retries.
        from reeldock.exporter import validate_image

        validate_image(body)
        self.cache.save_response(
            "tmdb:image", {"url": url}, {"key": self.cache.put(body)}, ttl=604800
        )
        return body

    async def series_details(self, *args):
        raise ProviderError("media_type_not_supported_p2")

    async def season_details(self, *args):
        raise ProviderError("media_type_not_supported_p2")

    async def episode_details(self, *args):
        raise ProviderError("media_type_not_supported_p2")
