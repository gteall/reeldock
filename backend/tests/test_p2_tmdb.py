import httpx
import pytest
from p2_support import jpeg

from reeldock.cache import Cache
from reeldock.domain import AppConfig, ProviderError
from reeldock.providers.tmdb import IMAGE, TMDBProvider


def factory(foundation, tmp_path, config_body, handle, **kwargs):
    return TMDBProvider(
        AppConfig(**config_body),
        Cache(foundation[0], tmp_path / "images"),
        transport=httpx.MockTransport(handle),
        **kwargs,
    )


def configuration():
    return {
        "images": {
            "secure_base_url": IMAGE,
            "poster_sizes": ["w500", "original"],
            "backdrop_sizes": ["w1280", "original"],
            "profile_sizes": ["w185", "original"],
        }
    }


async def test_field_fallback_original_language_and_persistent_cache(
    foundation, tmp_path, config_body
):
    calls = []

    def handle(request):
        calls.append((request.url.path, request.url.params.get("language")))
        assert request.headers["Authorization"] == "Bearer tmdb-private-token"
        language = request.url.params.get("language")
        if request.url.path.endswith("/credits"):
            body = {"cast": [], "crew": [{"job": "Director", "name": "A Director"}]}
        else:
            body = {
                "id": 550,
                "title": "中文标题",
                "original_title": "Original",
                "original_language": "en",
                "release_date": "1999-10-15",
                "overview": "",
                "tagline": "",
                "genres": [],
            }
            if language == "en":
                body.update(
                    overview="English plot",
                    tagline="",
                    genres=[{"name": "Drama"}],
                    original_language="zh",
                )
            if language == "en-US":
                body.update(
                    tagline="English tagline", overview="Do not replace", original_language="cn"
                )
        return httpx.Response(200, json=body)

    async with factory(foundation, tmp_path, config_body, handle) as provider:
        movie = await provider.movie_details("550")
        assert movie.title == "中文标题" and movie.overview == "English plot"
        assert movie.tagline == "English tagline" and movie.genres == ["Drama"]
        assert movie.original_language == "en" and movie.directors == ["A Director"]
    before = list(calls)
    async with factory(foundation, tmp_path, config_body, handle) as restarted:
        assert (await restarted.movie_details("550")).original_language == "en"
    assert calls == before


async def test_language_artwork_fallback_and_cdn_never_has_credentials(
    foundation, tmp_path, config_body
):
    downloaded = []

    def handle(request):
        if request.url.host == "image.tmdb.org":
            assert "authorization" not in request.headers and "api_key" not in request.url.params
            downloaded.append(request.url.path)
            return httpx.Response(200, content=jpeg())
        if request.url.path.endswith("/configuration"):
            body = configuration()
        elif request.url.path.endswith("/images"):
            assert "language" not in request.url.params
            body = {
                "posters": [
                    {"file_path": "/english.jpg", "iso_639_1": "en", "vote_average": 8},
                    {"file_path": "/neutral.jpg", "iso_639_1": None, "vote_average": 7},
                ],
                "backdrops": [{"file_path": "/bg.jpg", "iso_639_1": None}],
            }
        else:
            body = {"id": 550, "title": "电影", "original_language": "ja"}
        return httpx.Response(200, json=body)

    async with factory(foundation, tmp_path, config_body, handle) as provider:
        artwork = await provider.images("550")
        assert artwork[0].external_id == "/neutral.jpg"
        assert artwork[-1].kind == "fanart"
        assert await provider.download_artwork(artwork[0]) == jpeg()
        assert await provider.download_artwork(artwork[0]) == jpeg()
    assert downloaded == ["/t/p/w500/neutral.jpg"]


@pytest.mark.parametrize("delay,attempts,success", [("0", 2, True), ("30", 1, False)])
async def test_rate_limit_retry_after_is_bounded(
    foundation, tmp_path, config_body, delay, attempts, success
):
    calls, waits = [], []

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": delay})
            if len(calls) == 1
            else httpx.Response(200, json={"results": []})
        )

    async def sleep(seconds):
        waits.append(seconds)

    async with factory(foundation, tmp_path, config_body, handle, sleep=sleep) as provider:
        if success:
            assert await provider.search("Movie") == []
        else:
            with pytest.raises(ProviderError, match="tmdb_rate_limited"):
                await provider.search("Movie")
    assert len(calls) == attempts and len(waits) == int(success)


async def test_bad_image_does_not_poison_cache(foundation, tmp_path, config_body):
    count = 0

    def handle(request):
        nonlocal count
        if request.url.host == "image.tmdb.org":
            count += 1
            return httpx.Response(200, content=b"<html>bad</html>" if count == 1 else jpeg())
        return httpx.Response(200, json=configuration())

    async with factory(foundation, tmp_path, config_body, handle) as provider:
        art = await provider.artwork("/poster.jpg", "poster")
        with pytest.raises(ProviderError, match="invalid_image"):
            await provider.download_artwork(art)
        assert await provider.download_artwork(art) == jpeg()
    assert count == 2


async def test_api_key_and_image_origin_rejection(foundation, tmp_path, config_body):
    config_body["tmdb_token"] = "a" * 32

    def handle(request):
        assert request.url.params.get("api_key") == "a" * 32
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"images": {"secure_base_url": "https://evil.example/"}})

    async with factory(foundation, tmp_path, config_body, handle) as provider:
        with pytest.raises(ProviderError, match="tmdb_image_origin_rejected"):
            await provider.artwork("/poster.jpg", "poster")


async def test_imdb_lookup_uses_movie_results_only(foundation, tmp_path, config_body):
    def handle(request):
        assert request.url.path == "/3/find/tt0137523"
        assert request.url.params["external_source"] == "imdb_id"
        return httpx.Response(
            200,
            json={
                "movie_results": [{"id": 550, "title": "搏击俱乐部"}],
                "tv_results": [{"id": 999, "name": "Wrong type"}],
            },
        )

    async with factory(foundation, tmp_path, config_body, handle) as provider:
        assert (await provider.find_movie("tt0137523")).external_id == "550"
