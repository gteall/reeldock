"""HTTPX WebDAV transport, carrying forward the P0 protocol findings.

Small assets are create-only. MOVE requires an explicitly verified storage scope.
"""

import re
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from defusedxml import ElementTree

from reeldock.domain import AppConfig, ProviderError, RemoteEntry, normalize_path

DAV = "{DAV:}"
PROPERTIES = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/>
<d:getetag/><d:getlastmodified/></d:prop></d:propfind>"""


def origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    return (
        parsed.scheme,
        parsed.hostname or "",
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def status_error(status: int) -> ProviderError:
    code = {
        401: "storage_authentication_failed",
        403: "storage_permission_denied",
        404: "storage_not_found",
        405: "storage_not_webdav",
        409: "storage_conflict",
        412: "storage_conflict",
        429: "storage_rate_limited",
    }.get(status, "storage_http_error")
    return ProviderError(code, retryable=status == 429 or status >= 500)


class WebDAVProvider:
    def __init__(self, config: AppConfig, *, transport=None):
        self.config = config
        self.base = config.webdav_url
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.http_timeout_seconds),
            proxy=config.proxy_url,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            limits=httpx.Limits(max_connections=8),
        )
        secret = config.webdav_password.get_secret_value() if config.webdav_password else ""
        self.authorization = httpx.BasicAuth(config.webdav_username, secret)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()

    def url(self, path: str) -> str:
        path = normalize_path(path)
        return self.base + quote(path.lstrip("/"), safe="/")

    @asynccontextmanager
    async def response(self, method: str, path: str, *, headers=None, content=None):
        target = self.url(path)
        request_headers = {"Accept-Encoding": "identity", **(headers or {})}
        crossed_origin = False
        try:
            for _ in range(6):
                # HTTPX's shared cookie jar must never forward cookies to a CDN.
                self.client.cookies.clear()
                request = self.client.build_request(
                    method, target, headers=request_headers, content=content
                )
                if crossed_origin:
                    for name in (
                        "authorization",
                        "cookie",
                        "proxy-authorization",
                        "if-match",
                        "if-range",
                        "if-unmodified-since",
                    ):
                        request.headers.pop(name, None)
                response = await self.client.send(
                    request,
                    stream=True,
                    auth=None if crossed_origin else self.authorization,
                )
                if response.status_code not in {301, 302, 303, 307, 308}:
                    try:
                        yield response
                    finally:
                        await response.aclose()
                    return
                location = response.headers.get("location")
                await response.aclose()
                if method not in {"GET", "HEAD"} or not location:
                    raise ProviderError("storage_redirect_rejected")
                next_url = urljoin(target, location)
                parsed = urlsplit(next_url)
                if (
                    parsed.scheme not in {"http", "https"}
                    or parsed.username
                    or parsed.password
                    or parsed.fragment
                    or (urlsplit(target).scheme == "https" and parsed.scheme != "https")
                ):
                    raise ProviderError("storage_redirect_rejected")
                if origin(next_url) != origin(self.base):
                    if parsed.hostname not in self.config.redirect_hosts:
                        raise ProviderError("storage_redirect_host_denied")
                    crossed_origin = True
                target = next_url
            raise ProviderError("storage_redirect_limit")
        except httpx.TimeoutException:
            raise ProviderError("storage_timeout", retryable=True) from None
        except httpx.HTTPError:
            raise ProviderError("storage_network_error", retryable=True) from None

    @staticmethod
    async def bounded(response: httpx.Response, max_bytes: int) -> bytes:
        if response.headers.get("content-encoding", "identity") != "identity":
            raise ProviderError("storage_encoding_rejected")
        declared = response.headers.get("content-length")
        if declared:
            try:
                if int(declared) < 0 or int(declared) > max_bytes:
                    raise ProviderError("storage_byte_budget")
            except ValueError:
                raise ProviderError("storage_invalid_length") from None
        chunks, count = [], 0
        async for chunk in response.aiter_bytes(chunk_size=4096):
            count += len(chunk)
            if count > max_bytes:
                raise ProviderError("storage_byte_budget")
            chunks.append(chunk)
        if declared and count != int(declared):
            raise ProviderError("storage_short_read", retryable=True)
        return b"".join(chunks)

    async def _propfind(self, path: str, depth: int) -> list[RemoteEntry]:
        async with self.response(
            "PROPFIND",
            path,
            headers={
                "Depth": str(depth),
                "Content-Type": "application/xml; charset=utf-8",
            },
            content=PROPERTIES,
        ) as response:
            if response.status_code != 207:
                raise status_error(response.status_code)
            body = await self.bounded(response, 2 * 1024 * 1024)
        try:
            root = ElementTree.fromstring(body)
            if root.tag != DAV + "multistatus":
                raise ValueError()
            entries = []
            base_path = unquote(urlsplit(self.base).path).rstrip("/")
            for row in root.findall(DAV + "response"):
                href = row.findtext(DAV + "href")
                if not href:
                    raise ValueError()
                absolute = urljoin(self.base, href)
                raw_path = unquote(urlsplit(absolute).path).rstrip("/")
                if origin(absolute) != origin(self.base) or not (
                    raw_path == base_path or raw_path.startswith(base_path + "/")
                ):
                    raise ValueError()
                statuses = row.findall(DAV + "status")
                propstats = row.findall(DAV + "propstat")
                statuses += [item.find(DAV + "status") for item in propstats]
                if not statuses:
                    raise ValueError()
                for status in statuses:
                    if status is None:
                        raise ValueError()
                    match = re.fullmatch(r"HTTP/\S+ (\d{3})(?: .*)?", status.text or "")
                    if not match:
                        raise ValueError()
                    # Missing optional DAV properties are normal; a resource-level 404 is not.
                    if int(match[1]) == 404 and status not in row.findall(DAV + "status"):
                        continue
                    if not 200 <= int(match[1]) < 300:
                        raise status_error(int(match[1]))
                props = [
                    item.find(DAV + "prop")
                    for item in propstats
                    if " 2" in (item.findtext(DAV + "status") or "")
                ]
                props = [prop for prop in props if prop is not None]
                if not props:
                    raise ValueError()
                values = {child.tag: child for prop in props for child in prop}
                resource_type = values.get(DAV + "resourcetype")
                if resource_type is None:
                    raise ValueError()
                length = values.get(DAV + "getcontentlength")
                size = int(length.text) if length is not None and length.text else None
                if size is not None and size < 0:
                    raise ValueError()
                etag, modified = values.get(DAV + "getetag"), values.get(DAV + "getlastmodified")
                entries.append(
                    RemoteEntry(
                        path=normalize_path(raw_path[len(base_path) :] or "/"),
                        is_dir=resource_type.find(DAV + "collection") is not None,
                        size=size,
                        etag=etag.text if etag is not None else None,
                        modified=modified.text if modified is not None else None,
                    )
                )
            requested = normalize_path(path)
            if not any(entry.path == requested for entry in entries):
                raise ValueError()
            for entry in entries:
                suffix = entry.path[len(requested.rstrip("/")) + 1 :]
                if entry.path != requested and (
                    depth == 0
                    or "/" in suffix
                    or not entry.path.startswith(requested.rstrip("/") + "/")
                ):
                    raise ValueError()
            return entries
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("storage_invalid_multistatus") from None

    async def list(self, path: str) -> list[RemoteEntry]:
        entries = await self._propfind(path, 1)
        if not next(entry for entry in entries if entry.path == normalize_path(path)).is_dir:
            raise ProviderError("storage_not_directory")
        return [entry for entry in entries if entry.path != normalize_path(path)]

    async def stat(self, path: str) -> RemoteEntry:
        return (await self._propfind(path, 0))[0]

    async def read_small(self, path: str, max_bytes: int) -> bytes:
        async with self.response("GET", path) as response:
            if response.status_code != 200:
                raise status_error(response.status_code)
            return await self.bounded(response, max_bytes)

    async def read_range(self, path: str, start: int, length: int, size: int) -> bytes:
        if start < 0 or length <= 0 or start + length > size:
            raise ProviderError("storage_invalid_range")
        end = start + length - 1
        async with self.response(
            "GET", path, headers={"Range": f"bytes={start}-{end}"}
        ) as response:
            if response.status_code == 200:
                raise ProviderError("storage_range_ignored")
            if response.status_code != 206:
                raise status_error(response.status_code)
            if response.headers.get("content-range") != f"bytes {start}-{end}/{size}":
                raise ProviderError("storage_invalid_content_range")
            body = await self.bounded(response, length)
            if len(body) != length:
                raise ProviderError("storage_short_read", retryable=True)
            return body

    async def put(self, path: str, content: bytes) -> None:
        from pathlib import PurePosixPath

        name = PurePosixPath(path)
        allowed = name.suffix.lower() in {".nfo", ".jpg", ".srt", ".ass", ".ssa"}
        allowed |= (
            name.suffix == ".json"
            and name.parent.name == ".reeldock"
            and name.name.startswith("manifest-v")
        )
        if not allowed or len(content) > 20 * 1024 * 1024:
            raise ProviderError("storage_asset_write_rejected")
        # The real OpenList target ignores If-None-Match on PUT. Always reject an
        # existing resource ourselves too. This does not promise atomic exclusion
        # against unrelated external writers; packages must be exclusively managed.
        try:
            await self.stat(path)
        except ProviderError as error:
            if error.code != "storage_not_found":
                raise
        else:
            raise ProviderError("storage_conflict")
        async with self.response(
            "PUT", path, headers={"If-None-Match": "*"}, content=content
        ) as response:
            if response.status_code not in {200, 201, 204}:
                raise status_error(response.status_code)

    async def mkdir(self, path: str) -> None:
        async with self.response("MKCOL", path) as response:
            if response.status_code not in {200, 201, 204, 405}:
                raise status_error(response.status_code)
        entry = await self.stat(path)
        if not entry.is_dir:
            raise ProviderError("storage_conflict")

    async def move(self, source: str, target: str) -> None:
        if not self.config.move_verified:
            raise ProviderError("move_capability_unverified")
        source, target = normalize_path(source), normalize_path(target)
        if not source.startswith(self.config.input_path + "/") or not target.startswith(
            self.config.output_path + "/"
        ):
            raise ProviderError("archive_path_outside_verified_scope")
        try:
            await self.stat(target)
        except ProviderError as error:
            if error.code != "storage_not_found":
                raise
        else:
            raise ProviderError("storage_conflict")
        async with self.response(
            "MOVE", source, headers={"Destination": self.url(target), "Overwrite": "F"}
        ) as response:
            if response.status_code == 207:
                body = await self.bounded(response, 2 * 1024 * 1024)
                try:
                    root = ElementTree.fromstring(body)
                    statuses = root.findall(".//" + DAV + "status")
                    if root.tag != DAV + "multistatus" or not statuses:
                        raise ValueError()
                    codes = []
                    for status in statuses:
                        match = re.fullmatch(r"HTTP/\S+ (\d{3})(?: .*)?", status.text or "")
                        if not match:
                            raise ValueError()
                        codes.append(int(match[1]))
                    if any(not 200 <= code < 300 for code in codes):
                        failed = []
                        for row in root.findall(DAV + "response"):
                            row_codes = [
                                int(re.fullmatch(r"HTTP/\S+ (\d{3})(?: .*)?", s.text or "")[1])
                                for s in row.findall(".//" + DAV + "status")
                            ]
                            if any(not 200 <= code < 300 for code in row_codes):
                                href = urljoin(self.base, row.findtext(DAV + "href") or "")
                                prefix = unquote(urlsplit(self.url(source)).path).rstrip("/") + "/"
                                path = unquote(urlsplit(href).path)
                                if origin(href) == origin(self.base) and path.startswith(prefix):
                                    failed.append(
                                        normalize_path("/" + path[len(prefix) :]).lstrip("/")
                                    )
                        raise ProviderError(
                            "storage_move_partial_failure",
                            details={
                                "failed_paths": failed[:100],
                                "failure_count": sum(not 200 <= c < 300 for c in codes),
                            },
                        )
                except ProviderError:
                    raise
                except Exception:
                    raise ProviderError("storage_invalid_multistatus") from None
            elif response.status_code in {405, 501}:
                raise ProviderError("storage_move_unsupported")
            elif response.status_code not in {200, 201, 204}:
                raise status_error(response.status_code)

    async def capabilities(self) -> dict[str, bool]:
        return {
            "list": True,
            "stat": True,
            "write_enabled": True,
            "create_only": True,
            "conditional_create_verified": False,
            "concurrent_external_writes_safe": False,
            "move_enabled": self.config.move_verified,
            "remote_move_verified": self.config.move_verified,
        }
