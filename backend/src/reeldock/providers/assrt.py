"""ASSRT keyword search and individual subtitle files, never archive extraction."""

import json
import re
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlsplit

import httpx

from reeldock.domain import ProviderError
from reeldock.episodes import MULTI, PAIR, parse_episode
from reeldock.providers.webdav import WebDAVProvider
from reeldock.subtitles import MAX_SUBTITLE

API = "https://api.assrt.net/v1/sub/"
FORMATS = {"srt", "ass", "ssa"}


def language_rank(value):
    if re.search(
        r"简体|簡體|简中|簡中|简英|簡英|simplified|(?<![a-z])chs(?![a-z])|zh[-_](?:hans|cn)",
        value,
        re.I,
    ):
        return 0
    if re.search(
        r"繁体|繁體|繁中|繁英|traditional|(?<![a-z])cht(?![a-z])|zh[-_](?:hant|tw)", value, re.I
    ):
        return 2
    if re.search(r"双语|雙語|中英|中文|chinese|bilingual", value, re.I):
        return 1
    return 3


def episode_pairs(value):
    return {
        tuple(int(group) for group in match.groups() if group is not None)
        for match in PAIR.finditer(value)
    }


def episode_file_matches(name, description, context):
    if context.get("season") is None or context.get("episode") is None:
        return True
    target = context["season"], context["episode"]
    normalized = name.replace("\\", "/").lstrip("/")
    try:
        return parse_episode(f"Season {target[0]:02d}/" + normalized, ".") == target
    except ProviderError as error:
        if error.code != "episode_number_unknown":
            return False
        # Generic language filenames can inherit an explicit single-episode release.
        return not MULTI.search(description) and episode_pairs(description) == {target}


class AssrtProvider:
    def __init__(self, config, *, transport=None):
        self.config = config
        self.client = httpx.AsyncClient(
            timeout=config.http_timeout_seconds,
            proxy=config.proxy_url,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    async def request(self, action, params):
        if not self.config.assrt_token or not self.config.assrt_token.get_secret_value():
            raise ProviderError("assrt_token_required")
        try:
            self.client.cookies.clear()
            async with self.client.stream(
                "GET",
                API + action,
                params=params,
                headers={
                    "Authorization": "Bearer " + self.config.assrt_token.get_secret_value(),
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if response.status_code in {401, 403}:
                    raise ProviderError("assrt_token_invalid")
                if response.status_code == 429:
                    raise ProviderError("assrt_rate_limited", retryable=True)
                if response.status_code != 200:
                    raise ProviderError("assrt_unavailable", retryable=response.status_code >= 500)
                raw = await WebDAVProvider.bounded(response, 2 * 1024 * 1024)
            data = json.loads(raw)
            status = data.get("status")
            if status in {1, 20001}:
                raise ProviderError("assrt_token_invalid")
            if status == 30900:
                raise ProviderError("assrt_rate_limited", retryable=True)
            if status == 101:
                raise ProviderError("subtitle_keyword_too_short")
            if status == 20900:
                return []
            if status != 0:
                raise ProviderError("assrt_unavailable")
            rows = data.get("sub", {}).get("subs", [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError()
            return rows
        except httpx.TimeoutException:
            raise ProviderError("subtitle_timeout", retryable=True) from None
        except httpx.HTTPError:
            raise ProviderError("subtitle_network_error", retryable=True) from None
        except (ValueError, TypeError, AttributeError):
            raise ProviderError("subtitle_invalid_candidates") from None

    async def search(self, keyword, *, filename=False):
        params = {"q": keyword, "cnt": 15}
        if filename:
            params["no_muxer"] = 1
        rows = await self.request("search", params)
        results = []
        for row in rows[:15]:
            identifier = str(row.get("id", ""))
            if not identifier.isascii() or not identifier.isdecimal() or len(identifier) > 12:
                continue
            languages = row.get("lang") or {}
            results.append(
                {
                    "id": identifier,
                    "title": str(row.get("native_name") or row.get("videoname") or identifier)[
                        :512
                    ],
                    "release": str(row.get("videoname") or "")[:512],
                    "language": str(languages.get("desc") or "")[:100]
                    if isinstance(languages, dict)
                    else "",
                    "format": str(row.get("subtype") or "")[:100],
                    "source": "assrt",
                }
            )
        return results

    async def files(self, identifier):
        rows = await self.request("detail", {"id": identifier})
        row = next((row for row in rows if str(row.get("id")) == identifier), None)
        if row is None:
            raise ProviderError("subtitle_candidate_expired")
        files = row.get("filelist") or []
        if not isinstance(files, list):
            raise ProviderError("subtitle_invalid_candidates")
        # Some single-file uploads expose only filename/url on the detail object.
        if not files and row.get("filename") and row.get("url"):
            files = [{"f": row["filename"], "url": row["url"]}]
        results = []
        for index, item in enumerate(files[:200]):
            if not isinstance(item, dict):
                continue
            name = str(item.get("f") or "")
            extension = PurePosixPath(name).suffix.lower().lstrip(".")
            if extension not in FORMATS or not item.get("url"):
                continue
            results.append(
                {"index": index, "name": name[:512], "format": extension, "url": str(item["url"])}
            )
        return results

    async def automatic_candidates(self, keyword, episode_context=None):
        context = episode_context or {}
        results = await self.search(keyword, filename=True)
        results.sort(key=lambda item: language_rank(item["language"] + " " + item["title"]))
        for result in results[:5]:
            description = result["title"] + " " + result["release"]
            pairs = episode_pairs(description)
            target = context.get("season"), context.get("episode")
            if (
                None not in target
                and len(pairs) == 1
                and not MULTI.search(description)
                and pairs != {target}
            ):
                continue
            try:
                files = await self.files(result["id"])
            except ProviderError as error:
                if error.code == "subtitle_candidate_expired":
                    continue
                raise
            files = [
                item for item in files if episode_file_matches(item["name"], description, context)
            ]
            files.sort(key=lambda item: language_rank(item["name"]))
            for item in files[:10]:
                yield {
                    "candidate_id": result["id"],
                    "index": item["index"],
                    "name": item["name"],
                    "format": item["format"],
                    "keyword": keyword,
                }

    @staticmethod
    def allowed(url):
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise ProviderError("subtitle_host_not_allowed") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not (parsed.hostname == "assrt.net" or parsed.hostname.endswith(".assrt.net"))
            or port not in {None, 443}
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ProviderError("subtitle_host_not_allowed")

    async def download(self, identifier, file_index, file_name):
        files = await self.files(identifier)
        selected = next(
            (item for item in files if item["index"] == file_index and item["name"] == file_name),
            None,
        )
        if selected is None:
            raise ProviderError("subtitle_candidate_expired")
        url = selected["url"]
        # Historic API examples used HTTP; use HTTPS for the same approved ASSRT host.
        if url.startswith("http://"):
            url = "https://" + url.removeprefix("http://")
        try:
            for _ in range(6):
                self.allowed(url)
                self.client.cookies.clear()
                async with self.client.stream(
                    "GET", url, headers={"Accept-Encoding": "identity"}
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if not response.headers.get("location"):
                            raise ProviderError("subtitle_redirect_rejected")
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        if response.status_code == 429:
                            raise ProviderError("assrt_rate_limited", retryable=True)
                        raise ProviderError(
                            "subtitle_http_error", retryable=response.status_code >= 500
                        )
                    return await WebDAVProvider.bounded(response, MAX_SUBTITLE)
            raise ProviderError("subtitle_redirect_rejected")
        except httpx.TimeoutException:
            raise ProviderError("subtitle_timeout", retryable=True) from None
        except httpx.HTTPError:
            raise ProviderError("subtitle_network_error", retryable=True) from None
