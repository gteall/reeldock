"""Historical Shooter JSON API; untrusted URLs never inherit storage credentials."""

import json
import re
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import SecretStr

from reeldock.domain import ProviderError, SubtitleCandidate
from reeldock.providers.webdav import WebDAVProvider
from reeldock.subtitles import MAX_SUBTITLE

ENDPOINT = "https://www.shooter.cn/api/subapi.php"


class ShooterProvider:
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

    def allowed(self, target):
        url = urlsplit(target)
        if (
            url.scheme != "https"
            or url.hostname not in self.config.subtitle_download_hosts
            or url.port not in {None, 443}
            or url.username
            or url.password
            or url.fragment
        ):
            raise ProviderError("subtitle_host_not_allowed")

    async def request(self, method, url, *, data=None, limit):
        try:
            for _ in range(6):
                self.allowed(url)
                self.client.cookies.clear()
                async with self.client.stream(
                    method, url, data=data, headers={"Accept-Encoding": "identity"}
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if method != "GET" or not response.headers.get("location"):
                            raise ProviderError("subtitle_redirect_rejected")
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code != 200:
                        raise ProviderError(
                            "subtitle_http_error",
                            retryable=response.status_code == 429 or response.status_code >= 500,
                        )
                    return await WebDAVProvider.bounded(response, limit)
            raise ProviderError("subtitle_redirect_rejected")
        except httpx.TimeoutException:
            raise ProviderError("subtitle_timeout", retryable=True) from None
        except httpx.HTTPError:
            raise ProviderError("subtitle_network_error", retryable=True) from None

    async def search(self, fingerprint, episode_context=None):
        if not re.fullmatch(r"[0-9a-f]{32}(;[0-9a-f]{32}){3}", fingerprint):
            raise ProviderError("shooter_hash_invalid")
        raw = await self.request(
            "POST",
            ENDPOINT,
            data={
                "filehash": fingerprint,
                "pathinfo": (episode_context or {}).get("filename", "video.mkv"),
                "format": "json",
                "lang": "Chn",
            },
            limit=1024 * 1024,
        )
        if raw.strip() in {b"\xff", b"-1", b"[]"}:
            return []
        try:
            rows = json.loads(raw)
            if not isinstance(rows, list):
                raise ValueError()

            def rank(row):
                description = str(row.get("Desc", "")).lower()
                if re.search(r"简体|簡體|中英|双语|雙語|simplified|chs", description):
                    return 0
                if re.search(r"繁体|繁體|traditional|cht", description):
                    return 2
                return 1

            if any(not isinstance(row, dict) for row in rows[:10]):
                raise ValueError()
            candidates = []
            for i, row in enumerate(sorted(rows[:10], key=rank)):
                delay = row.get("Delay", 0)
                if type(delay) is not int or abs(delay) > 600000:
                    raise ValueError()
                for j, item in enumerate(row.get("Files", [])[:10]):
                    extension = item["Ext"].lower().lstrip(".")
                    if extension not in {"srt", "ass", "ssa"}:
                        continue  # No automatic archive extraction or executable payloads.
                    self.allowed(item["Link"])
                    candidates.append(
                        SubtitleCandidate(
                            source="shooter",
                            external_id=f"{i}:{j}",
                            download_url=SecretStr(item["Link"]),
                            format=extension,
                            delay_ms=delay,
                        )
                    )
            return candidates
        except ProviderError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError("subtitle_invalid_candidates") from None

    async def download(self, candidate):
        return await self.request(
            "GET", candidate.download_url.get_secret_value(), limit=MAX_SUBTITLE
        )
