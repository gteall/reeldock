import httpx
import pytest

from reeldock.domain import AppConfig, ProviderError
from reeldock.providers.webdav import WebDAVProvider


def provider(config_body, handler):
    config = AppConfig(**{**config_body, "redirect_hosts": ["cdn.example"]})
    return WebDAVProvider(config, transport=httpx.MockTransport(handler))


def multistatus(*, status=200, href="/dav/incoming/movie%20%23%25.mkv", optional=True):
    optional_xml = (
        """<d:propstat><d:prop><d:getetag/></d:prop>
        <d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>"""
        if optional
        else ""
    )
    return f"""<d:multistatus xmlns:d="DAV:">
    <d:response><d:href>/dav/incoming/</d:href><d:propstat>
      <d:status>HTTP/1.1 200 OK</d:status><d:prop>
      <d:resourcetype><d:collection/></d:resourcetype></d:prop>
    </d:propstat></d:response>
    <d:response><d:href>{href}</d:href><d:propstat>
      <d:status>HTTP/1.1 {status} Test</d:status><d:prop><d:resourcetype/>
      <d:getcontentlength>12</d:getcontentlength></d:prop>
    </d:propstat>{optional_xml}</d:response></d:multistatus>""".encode()


async def test_list_special_names_and_optional_404(config_body):
    def handle(request):
        assert request.method == "PROPFIND" and request.headers["depth"] == "1"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(207, content=multistatus())

    async with provider(config_body, handle) as storage:
        entries = await storage.list("/incoming")
        assert entries[0].path == "/incoming/movie #%.mkv" and entries[0].size == 12
        assert (
            storage.url("/中文 #100%") == "https://dav.example/dav/%E4%B8%AD%E6%96%87%20%23100%25"
        )


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "storage_authentication_failed"),
        (403, "storage_permission_denied"),
        (404, "storage_not_found"),
        (405, "storage_not_webdav"),
        (429, "storage_rate_limited"),
        (500, "storage_http_error"),
    ],
)
async def test_safe_error_classification(config_body, status, code):
    async with provider(
        config_body, lambda _: httpx.Response(status, text="private URL and secret")
    ) as storage:
        with pytest.raises(ProviderError, match=code) as error:
            await storage.list("/incoming")
        assert error.value.retryable == (status >= 500 or status == 429)
        assert "private" not in str(error.value)


async def test_partial_permission_failure_is_not_hidden(config_body):
    async with provider(
        config_body, lambda _: httpx.Response(207, content=multistatus(status=403))
    ) as storage:
        with pytest.raises(ProviderError, match="storage_permission_denied"):
            await storage.list("/incoming")


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example/dav/incoming/movie.mkv",
        "/outside/movie.mkv",
        "/dav/incoming/deeper/movie.mkv",
        "/dav/incoming/%2e%2e/file",
    ],
)
async def test_invalid_hrefs_are_rejected(config_body, href):
    async with provider(
        config_body, lambda _: httpx.Response(207, content=multistatus(href=href))
    ) as storage:
        with pytest.raises(ProviderError, match="storage_invalid_multistatus"):
            await storage.list("/incoming")


async def test_cross_origin_strips_auth_cookies_and_dav_conditions(config_body):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == "dav.example":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://cdn.example/file?sign=private",
                    "Set-Cookie": "private=cookie; Domain=.example",
                },
            )
        assert "authorization" not in request.headers and "cookie" not in request.headers
        assert "if-match" not in request.headers and "if-range" not in request.headers
        assert request.headers["range"] == "bytes=0-3"
        return httpx.Response(206, content=b"abcd", headers={"Content-Range": "bytes 0-3/12"})

    async with provider(config_body, handle) as storage:
        async with storage.response(
            "GET",
            "/movie",
            headers={"Range": "bytes=0-3", "If-Match": "dav-etag", "If-Range": "dav-etag"},
        ) as response:
            assert await storage.bounded(response, 4) == b"abcd"
    assert len(requests) == 2


@pytest.mark.parametrize(
    "location,code",
    [
        ("https://evil.example/media", "storage_redirect_host_denied"),
        ("http://cdn.example/media", "storage_redirect_rejected"),
        ("https://user:secret@cdn.example/media", "storage_redirect_rejected"),
    ],
)
async def test_untrusted_redirects(config_body, location, code):
    async with provider(
        config_body, lambda _: httpx.Response(302, headers={"Location": location})
    ) as storage:
        with pytest.raises(ProviderError, match=code):
            await storage.read_small("/movie", 10)


async def test_propfind_redirect_is_not_followed(config_body):
    async with provider(
        config_body, lambda _: httpx.Response(302, headers={"Location": "https://cdn.example/"})
    ) as storage:
        with pytest.raises(ProviderError, match="storage_redirect_rejected"):
            await storage.list("/incoming")


class Unreadable(httpx.AsyncByteStream):
    async def __aiter__(self):
        pytest.fail("Response body must not be read")
        yield b""


@pytest.mark.parametrize(
    "status,headers,code",
    [
        (200, {}, "storage_range_ignored"),
        (206, {"Content-Range": "bytes 1-4/12"}, "storage_invalid_content_range"),
        (206, {"Content-Range": "bytes 0-3/12", "Content-Length": "12000"}, "storage_byte_budget"),
    ],
)
async def test_range_rejected_before_body(config_body, status, headers, code):
    async with provider(
        config_body, lambda _: httpx.Response(status, headers=headers, stream=Unreadable())
    ) as storage:
        with pytest.raises(ProviderError, match=code):
            await storage.read_range("/movie", 0, 4, 12)


async def test_range_success_short_read_and_timeout(config_body):
    async with provider(
        config_body,
        lambda _: httpx.Response(206, headers={"Content-Range": "bytes 0-3/12"}, content=b"abcd"),
    ) as storage:
        assert await storage.read_range("/movie", 0, 4, 12) == b"abcd"
    async with provider(
        config_body,
        lambda _: httpx.Response(206, headers={"Content-Range": "bytes 0-3/12"}, content=b"ab"),
    ) as storage:
        with pytest.raises(ProviderError, match="storage_short_read"):
            await storage.read_range("/movie", 0, 4, 12)

    def timeout(request):
        raise httpx.ReadTimeout("https://secret.example/?token=private")

    async with provider(config_body, timeout) as storage:
        with pytest.raises(ProviderError, match="storage_timeout"):
            await storage.list("/incoming")


async def test_media_writes_and_archive_stay_disabled(config_body):
    def forbidden(request):
        pytest.fail("No HTTP call allowed for P1 writes")

    async with provider(config_body, forbidden) as storage:
        with pytest.raises(ProviderError, match="storage_asset_write_rejected"):
            await storage.put("/file", b"data")
        with pytest.raises(ProviderError, match="archive_disabled_p1"):
            await storage.move("/source", "/target")
        assert not (await storage.capabilities())["move_enabled"]


async def test_p2_put_sends_condition_and_rejects_existing_before_write(config_body):
    calls = []
    exists = False

    def handle(request):
        nonlocal exists
        calls.append(request.method)
        if request.method == "PROPFIND":
            if not exists:
                return httpx.Response(404)
            return httpx.Response(
                207,
                content=b'<d:multistatus xmlns:d="DAV:"><d:response>'
                b"<d:href>/dav/incoming/poster.jpg</d:href><d:propstat>"
                b"<d:status>HTTP/1.1 200 OK</d:status><d:prop>"
                b"<d:resourcetype/></d:prop></d:propstat></d:response></d:multistatus>",
            )
        assert request.method == "PUT" and request.headers["If-None-Match"] == "*"
        exists = True
        return httpx.Response(201)

    async with provider(config_body, handle) as storage:
        await storage.put("/incoming/poster.jpg", b"bytes")
        with pytest.raises(ProviderError, match="storage_conflict"):
            await storage.put("/incoming/poster.jpg", b"other")
    assert calls == ["PROPFIND", "PUT", "PROPFIND"]
