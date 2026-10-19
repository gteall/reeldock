from pathlib import PurePosixPath

import pytest
from alembic import command
from alembic.config import Config
from p4_support import ROOT, run_tv, setup_tv
from sqlalchemy import select
from test_p4_api import scan, tv_app

from reeldock.domain import Movie, ProviderError
from reeldock.matching import (
    MAX_SEARCHES,
    ParsedName,
    automatic_candidate,
    parse_name,
    rank_candidates,
    search_candidates,
    search_queries,
)
from reeldock.models import MovieRecord, Package, Task

SQUID = "鱿鱼游戏 第二季[杜比视界版本][7全集]2021.2160p.NF.WEB-DL.DDP5.1.Atmos.DV.H.265-HHWEB"


@pytest.mark.parametrize(
    "name,title,year,season",
    [
        (SQUID, "鱿鱼游戏", 2021, 2),
        ("Show.2024.S02.1080p", "Show", 2024, 2),
        ("Show.Season.02.2024.1080p", "Show", 2024, 2),
        ("Show.S02E01.Pilot.1080p.mkv", "Show", None, 2),
        ("鱿鱼游戏 1-3季[杜比视界版本]", "鱿鱼游戏", None, 1),
        ("1917.mkv", "1917", None, None),
        ("1984.mkv", "1984", None, None),
        ("2001.A.Space.Odyssey.1968.1080p.mkv", "2001 A Space Odyssey", 1968, None),
        ("Blade.Runner.2049.2017.1080p.mkv", "Blade Runner 2049", 2017, None),
        ("庆余年2.mkv", "庆余年2", None, None),
        ("三体.S01E01.mkv", "三体", None, 1),
        ("【我推的孩子】.S02E01.mkv", "【我推的孩子】", None, 2),
        ("[未知版本]鱿鱼游戏.S02E01.mkv", "[未知版本]鱿鱼游戏", None, 2),
    ],
)
def test_structural_title_parsing_preserves_real_titles(name, title, year, season):
    parsed = parse_name(name, kind="tv")
    assert (parsed.title, parsed.year, parsed.season) == (title, year, season)


def test_full_titles_precede_partial_queries_and_library_root_is_excluded():
    root = "/incoming/鱿鱼游戏 1-3季"
    queries = search_queries(
        root + "/Season 02",
        [root + "/Season 02/Squid.Game.S02E01.mkv", root + "/Season 02/Squid.Game.S02E02.mkv"],
        "tv",
        root="/incoming",
    )
    assert [(q.parsed.title, q.source) for q in queries] == [
        ("Squid Game", "filename"),
        ("鱿鱼游戏", "parent"),
    ]
    queries = search_queries(
        "/incoming/无关目录", ["/incoming/无关目录/Show.S01E01.mkv"], "tv", root="/incoming"
    )
    assert all(q.parsed.title != "incoming" for q in queries)
    assert search_queries("/incoming/Show", [], "tv", override="自定义片名")[0].source == "manual"
    queries = search_queries(
        "/incoming/Movie (2020)", ["/incoming/Movie (2020)/Movie.mkv"], "movie"
    )
    assert len(queries) == 1 and queries[0].parsed.year == 2020


def test_bilingual_queries_are_bounded_and_unknown_brackets_are_weak():
    queries = search_queries(
        "/incoming/鱿鱼游戏 Squid Game",
        [f"/incoming/鱿鱼游戏 Squid Game/名称{i}.S01E0{i}.mkv" for i in range(1, 4)],
        "tv",
        root="/incoming",
    )
    assert len(queries) <= MAX_SEARCHES
    queries = search_queries("/incoming/[未知版本]鱿鱼游戏", [], "tv", root="/incoming")
    assert queries[0].parsed.title == "[未知版本]鱿鱼游戏"
    assert queries[1].parsed.title == "鱿鱼游戏" and queries[1].weak
    queries = search_queries("/incoming/鱿鱼游戏(Squid Game)", [], "tv")
    assert [(q.parsed.title, q.weak) for q in queries][1:] == [
        ("鱿鱼游戏", False),
        ("Squid Game", False),
    ]
    queries = search_queries("/incoming/Movie - Part Two", [], "movie")
    assert all(q.weak for q in queries[1:])


@pytest.mark.parametrize(
    "kind,year,weak,automatic",
    [
        ("movie", None, False, True),
        ("movie", 2024, False, False),
        ("tv", 2024, False, True),
        ("tv", 2021, True, False),
    ],
)
def test_title_without_year_and_series_season_year_have_useful_confidence(
    kind, year, weak, automatic
):
    rows = rank_candidates(
        ParsedName("鱿鱼游戏", year, None),
        [Movie(source="tmdb", external_id="93405", title="鱿鱼游戏", year=2021)],
        kind=kind,
        weak=weak,
    )
    assert (automatic_candidate(rows) == "93405") is automatic


async def test_search_fallback_merges_candidates_and_stops_after_reliable_match():
    calls, reports = [], []
    movie = Movie(source="tmdb", external_id="93405", title="鱿鱼游戏", year=2021)

    class Metadata:
        async def search(self, title, kind, year):
            calls.append(title)
            return [movie]

    queries = search_queries(
        "/incoming/未知剧目", ["/incoming/未知剧目/鱿鱼游戏.S02E01.mkv"], "tv", root="/incoming"
    )
    selected, rows = await search_candidates(
        Metadata(),
        queries,
        "tv",
        choose=automatic_candidate,
        report=lambda evidence, _: reports.append(evidence["outcome"]),
        check=lambda: None,
    )
    assert selected == "93405" and len(rows) == 1
    assert calls == ["未知剧目", "鱿鱼游戏"] and reports[-1] == "matched"


async def test_provider_failure_is_not_treated_as_no_results_or_retried_with_other_titles():
    calls, reports = [], []

    class Metadata:
        async def search(self, title, kind, year):
            calls.append(title)
            raise ProviderError("tmdb_timeout", retryable=True)

    with pytest.raises(ProviderError, match="tmdb_timeout"):
        await search_candidates(
            Metadata(),
            search_queries("/in/Unknown", ["/in/Unknown/Show.S01E01.mkv"], "tv"),
            "tv",
            choose=automatic_candidate,
            report=lambda evidence, _: reports.append(evidence["outcome"]),
            check=lambda: None,
        )
    assert len(calls) == 1 and reports[-1] == "search_failed"


async def test_screenshot_series_matches_from_original_path_without_renaming(foundation, tmp_path):
    env = await setup_tv(foundation, tmp_path, files=["鱿鱼游戏.S02E01.mkv", "鱿鱼游戏.S02E02.mkv"])
    db, store, queue, dav, metadata, _, _, worker, revision = env
    root = "/incoming/" + SQUID
    dav.directories = {"/incoming", root}
    dav.media = {path.replace(ROOT, root): size for path, size in dav.media.items()}
    original_media = dict(dav.media)
    metadata.series = metadata.series.model_copy(
        update={
            "external_id": "93405",
            "title": "鱿鱼游戏",
            "year": 2021,
        }
    )
    calls = []

    async def search(query, kind, year=None):
        calls.append((query, kind, year))
        return [metadata.series] if query == "鱿鱼游戏" else []

    metadata.search = search
    config, revision = store.load()
    await worker.movies.scanner.scan(dav, config, revision)
    with db.sessions.begin() as session:
        package = session.scalar(select(Package).where(Package.remote_path == root))
        row = session.get(MovieRecord, package.id)
        row.title = "旧版本保存的未清理标题"
    task_id = queue.submit("movie_base", root, "screenshot", revision)
    await worker.execute(queue.claim("screenshot"))
    with db.sessions.begin() as session:
        assert session.get(Task, task_id).status == "completed"
        row = session.get(MovieRecord, package.id)
        assert row.match_evidence["parsed_title"] == "鱿鱼游戏"
        assert row.match_evidence["season"] == 2
        assert row.match_evidence["outcome"] == "matched"
        assert session.get(Package, package.id).tmdb_id == "93405"
    assert calls == [("鱿鱼游戏", "tv", 2021)]
    assert dav.media == original_media and dav.calls["move"] == 0
    queue.submit("movie_base", root, "reuse-screenshot", revision)
    await worker.execute(queue.claim("reuse"))
    assert len(calls) == 1 and metadata.calls["series"] == 1


async def test_series_falls_back_to_episode_filename_and_distinguishes_empty_results(
    foundation, tmp_path
):
    env = await setup_tv(foundation, tmp_path, files=["Real.Show.S01E01.mkv"])
    metadata = env[4]
    metadata.series.title, metadata.series.year = "Real Show", None
    calls = []

    async def search(query, kind, year=None):
        calls.append(query)
        return [metadata.series] if query == "Real Show" else []

    metadata.search = search
    task, _ = await run_tv(env)
    assert task.status == "completed", task.error_code
    assert calls == ["Example", "Real Show"]


async def test_empty_results_and_stale_task_errors_after_rescan(runtime, config_body):
    with tv_app(runtime, config_body, ["Example.S01E01.mkv"]) as (app, client, dav, metadata, *_):

        async def empty(*args):
            return []

        metadata.search = empty
        await scan(app, dav)
        view = client.get("/api/packages").json()["items"][0]
        task_id = app.state.queue.submit(
            "movie_base", ROOT, "no-results", app.state.store.load()[1]
        )
        await app.state.worker.execute(app.state.queue.claim("no-results"))
        view = client.get("/api/packages/" + view["id"]).json()
        assert view["error_code"] == "tmdb_search_no_results"
        assert view["candidates"] == [] and view["match_evidence"]["outcome"] == "no_results"
        assert view["match_evidence"]["queries"][0]["result_count"] == 0
        video = next(iter(dav.media))
        dav.media[video] += 1
        await scan(app, dav)
        view = client.get("/api/packages/" + view["id"]).json()
        assert view["candidates"] == [] and view["error_code"] is None
        assert view["match_evidence"] == {} and view["task_id"] is None
        assert client.get("/api/tasks/" + task_id).json()["error_code"] == "tmdb_search_no_results"


async def test_search_override_is_persistent_versioned_and_does_not_modify_files(
    runtime, config_body
):
    with tv_app(runtime, config_body, ["Example.S01E01.mkv"]) as (app, client, dav, metadata, *_):
        await scan(app, dav)
        film = client.get("/api/packages").json()["items"][0]
        url = f"/api/packages/{film['id']}/search-query"
        body = {"query": " 自定义片名 ", "expected_context_version": film["context_version"]}
        assert client.put(url, json=body, headers={"X-CSRF-Token": "bad"}).status_code == 403
        assert client.put(url, json={**body, "query": "[]"}).status_code == 422
        assert client.put(url, json=body).status_code == 200
        assert client.put(url, json=body).status_code == 409
        film = client.get("/api/packages/" + film["id"]).json()
        assert film["search_query"] == "自定义片名" and film["match_status"] == "pending"
        assert film["context_version"] == body["expected_context_version"] + 1
        assert not dav.files
        calls = []

        async def search(query, kind, year=None):
            calls.append(query)
            return [metadata.series]

        metadata.search = search
        metadata.series.title = "自定义片名"
        app.state.queue.submit("movie_base", ROOT, "custom-search", app.state.store.load()[1])
        await app.state.worker.execute(app.state.queue.claim("custom-search"))
        film = client.get("/api/packages/" + film["id"]).json()
        assert film["match_status"] == "matched" and calls == ["自定义片名"]
        assert film["match_evidence"]["queries"][0]["source"] == "manual"
        assert all(PurePosixPath(path).suffix != ".mp4" for path in dav.files)
        existing = dict(dav.files)
        assert (
            client.put(
                url, json={"query": "", "expected_context_version": film["context_version"]}
            ).status_code
            == 200
        )
        assert client.get("/api/packages/" + film["id"]).json()["search_query"] is None
        assert dav.files == existing


def test_upgrade_from_previous_schema_preserves_saved_matches(foundation):
    import reeldock.database

    db, _, _ = foundation
    with db.sessions.begin() as session:
        package = Package(remote_path="/incoming/Existing", kind="tv", tmdb_id="93405")
        session.add(package)
        session.flush()
        package_id = package.id
        session.add(
            MovieRecord(
                package_id=package_id,
                title="鱿鱼游戏",
                match_status="matched",
                details={"external_id": "93405", "title": "鱿鱼游戏"},
            )
        )
    config = Config()
    config.set_main_option(
        "script_location", str(PurePosixPath(reeldock.database.__file__).parent / "migrations")
    )
    with db.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0004")
    db.migrate()
    with db.sessions.begin() as session:
        record = session.get(MovieRecord, package_id)
        assert record.match_status == "matched" and record.details["external_id"] == "93405"
        assert record.search_query is None and record.match_evidence == {}
        assert session.get(Package, package_id).tmdb_id == "93405"
