import httpx
import pytest
from test_p2_tmdb import configuration, factory

from reeldock.domain import ProviderError


async def test_tv_endpoints_inherit_original_language_and_cache(foundation, tmp_path, config_body):
    calls = []

    def handle(request):
        path, lang = request.url.path, request.url.params.get("language")
        calls.append((path, lang))
        if path.endswith("configuration"):
            value = configuration()
            value["images"]["still_sizes"] = ["w300", "original"]
        elif path.endswith("/images"):
            value = {"posters": [{"file_path": "/season.jpg", "iso_639_1": "zh"}], "stills": []}
        elif "/episode/" in path:
            value = {
                "id": 101,
                "name": "中文单集",
                "season_number": 0,
                "episode_number": 1,
                "air_date": "2020-01-02",
                "overview": "" if lang == "zh-CN" else "Fallback",
                "original_language": "zh",
                "still_path": "/still.jpg",
            }
        elif "/season/" in path:
            value = {
                "season_number": 0,
                "name": "Specials",
                "overview": "Season plot",
                "episodes": [],
            }
        elif "/search/" in path:
            value = {
                "results": [
                    {
                        "id": 123,
                        "name": "Example",
                        "original_language": "en",
                        "first_air_date": "2020-01-01",
                    }
                ]
            }
        else:
            value = {
                "id": 123,
                "name": "中文剧",
                "overview": "" if lang == "zh-CN" else "Original plot",
                "genres": [{"name": "Drama"}],
                "original_language": "en" if lang == "zh-CN" else "cn",
            }
        return httpx.Response(200, json=value)

    async with factory(foundation, tmp_path, config_body, handle) as provider:
        assert (await provider.search("Example", "tv"))[0].year == 2020
        series = await provider.series_details("123")
        assert series.original_language == "en" and series.overview == "Original plot"
        assert (await provider.season_details("123", 0)).season == 0
        episode = await provider.episode_details("123", 0, 1)
        assert episode.original_language == "en" and episode.overview == "Fallback"
        assert (await provider.images("123", "tv", season=0))[0].kind == "season_poster"
        thumb = (await provider.images("123", "tv", season=0, episode=1))[0]
        assert thumb.kind == "thumb" and thumb.url.get_secret_value().endswith("/w300/still.jpg")
        before = list(calls)
        await provider.episode_details("123", 0, 1)
        assert calls == before
    assert ("/3/tv/123/season/0/episode/1", "en") in calls


async def test_tv_episode_identity_conflict(foundation, tmp_path, config_body):
    def handle(request):
        return httpx.Response(
            200,
            json=(
                {
                    "id": 123,
                    "name": "Show",
                    "overview": "x",
                    "genres": [{"name": "Drama"}],
                    "original_language": "en",
                }
                if request.url.path == "/3/tv/123"
                else {
                    "id": 10,
                    "name": "Wrong",
                    "season_number": 1,
                    "episode_number": 2,
                    "overview": "x",
                }
            ),
        )

    async with factory(foundation, tmp_path, config_body, handle) as provider:
        with pytest.raises(ProviderError, match="tmdb_episode_identity_conflict"):
            await provider.episode_details("123", 1, 1)


async def test_shooter_rejects_explicit_other_episode_candidates():
    from reeldock.domain import AppConfig
    from reeldock.providers.shooter import ShooterProvider

    def handle(request):
        return httpx.Response(
            200,
            json=[
                {
                    "Desc": "Show S01E02 简体",
                    "Files": [{"Ext": "srt", "Link": "https://shooter.cn/wrong.srt"}],
                },
                {
                    "Desc": "Show S01E01 简体",
                    "Files": [{"Ext": "srt", "Link": "https://shooter.cn/right.srt"}],
                },
            ],
        )

    config = AppConfig(webdav_url="https://dav.example/dav/", input_path="/in", output_path="/out")
    async with ShooterProvider(config, transport=httpx.MockTransport(handle)) as provider:
        candidates = await provider.search(
            ";".join(["a" * 32] * 4), {"filename": "Show.S01E01.mkv", "season": 1, "episode": 1}
        )
    assert len(candidates) == 1 and candidates[0].download_url.get_secret_value().endswith(
        "/right.srt"
    )
