"""Provider-neutral models and P1 phase gates. No media probing lives here."""

import posixpath
import re
from enum import StrEnum
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


def normalize_path(value: str) -> str:
    value = value.strip()
    # Configuration uses decoded paths. Reject traversal, including encoded traversal.
    decoded = value
    for _ in range(4):
        new = unquote(decoded)
        if new == decoded:
            break
        decoded = new
    for candidate in (value, decoded):
        if "\\" in candidate or any(ord(c) < 32 for c in candidate):
            raise ValueError("路径包含非法字符")
        if any(part in {".", ".."} for part in candidate.split("/")):
            raise ValueError("路径不得包含 . 或 ..")
    if not value.startswith("/"):
        raise ValueError("请填写以 / 开始的 WebDAV 相对路径")
    return posixpath.normpath("/" + value.lstrip("/"))


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("请填写无凭证、查询参数或片段的 HTTP(S) URL")
    return value.rstrip("/") + "/"


class AppConfig(BaseModel):
    webdav_url: str
    webdav_username: str = Field(default="", max_length=256)
    webdav_password: SecretStr | None = None
    scrape_paths: list[str] = Field(min_length=1, max_length=32)
    tmdb_token: SecretStr | None = None
    assrt_token: SecretStr | None = None
    proxy_url: str | None = None
    tmdb_proxy_url: str | None = None
    # Empty follows the configured WebDAV's download redirects; nonempty restricts hosts.
    redirect_hosts: list[str] = Field(default_factory=list, max_length=20)
    http_timeout_seconds: float = Field(default=15, ge=1, le=120)
    probe_timeout_seconds: int = Field(default=45, ge=1, le=300)
    probe_max_bytes: int = Field(default=67108864, ge=16384, le=1073741824)
    chinese_languages: list[str] = Field(default_factory=lambda: ["zh", "cn"], min_length=1)
    policy_version: int = Field(default=1, ge=1)
    stable_seconds: int = Field(default=600, ge=1, le=86400)
    actor_limit: int = Field(default=20, ge=0, le=500)
    scrape_actor_images: bool = False
    actor_policy: str = Field(default="available_only", pattern=r"^(available_only|strict)$")
    subtitle_download_hosts: list[str] = Field(
        default_factory=lambda: ["www.shooter.cn", "shooter.cn"], max_length=20
    )

    _url = field_validator("webdav_url")(validate_url)

    @model_validator(mode="before")
    @classmethod
    def legacy_paths(cls, value):
        if isinstance(value, dict) and "scrape_paths" not in value and value.get("input_path"):
            value = {**value, "scrape_paths": [value["input_path"]]}
        return value

    @field_validator("scrape_paths")
    @classmethod
    def directories(cls, values: list[str]) -> list[str]:
        paths = list(dict.fromkeys(normalize_path(value) for value in values))
        if "/" in paths:
            raise ValueError("刮削目录不能使用 WebDAV 根目录")
        return paths

    def root_for(self, path: str) -> str | None:
        return next(
            (root for root in self.scrape_paths if path == root or path.startswith(root + "/")),
            None,
        )

    @property
    def scan_roots(self) -> list[str]:
        return [
            path
            for path in self.scrape_paths
            if not any(path != root and path.startswith(root + "/") for root in self.scrape_paths)
        ]

    @field_validator("proxy_url", "tmdb_proxy_url")
    @classmethod
    def proxy(cls, value: str | None) -> str | None:
        return validate_url(value) if value else None

    @field_validator("redirect_hosts", "subtitle_download_hosts")
    @classmethod
    def hosts(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in value):
                raise ValueError("重定向白名单只接受小写的精确主机名")
        return sorted(set(values))

    @field_validator("chinese_languages")
    @classmethod
    def languages(cls, values: list[str]) -> list[str]:
        result = sorted({value.strip().lower() for value in values})
        if any(not re.fullmatch(r"[a-z]{2,3}", value) for value in result):
            raise ValueError("TMDB 语言集合必须是两位或三位语言代码，例如 zh, cn")
        return result


class ConfigUpdate(AppConfig):
    expected_revision: int = Field(default=0, ge=0)
    clear_webdav_password: bool = False
    clear_tmdb_token: bool = False
    clear_assrt_token: bool = False


class Stage(StrEnum):
    MATCH = "match_metadata"
    BASE = "base_assets_verified"
    SUBTITLE = "subtitle_policy"
    MANIFEST = "final_manifest"
    ARCHIVE = "archive"


STAGES = (Stage.MATCH, Stage.BASE, Stage.SUBTITLE, Stage.MANIFEST)
BASE_KINDS = frozenset({"nfo", "poster", "fanart", "actor"})


class RemoteEntry(BaseModel):
    path: str
    is_dir: bool
    size: int | None = None
    etag: str | None = None
    modified: str | None = None


class Artwork(BaseModel):
    source: str
    external_id: str
    kind: str
    url: SecretStr | None = None
    language: str | None = None


class Person(BaseModel):
    source: str
    external_id: str
    name: str
    profile: Artwork | None = None
    role: str = ""
    order: int = 0


class Movie(BaseModel):
    source: str
    external_id: str
    title: str
    original_language: str | None = None
    year: int | None = None
    imdb_id: str | None = None
    original_title: str = ""
    overview: str = ""
    tagline: str = ""
    release_date: str = ""
    genres: list[str] = Field(default_factory=list)
    rating: float | None = None
    votes: int = 0
    directors: list[str] = Field(default_factory=list)
    writers: list[str] = Field(default_factory=list)
    poster_path: str | None = None
    backdrop_path: str | None = None


class Series(Movie):
    pass


class Episode(Movie):
    series_id: str
    season: int
    episode: int
    still_path: str | None = None


class Season(BaseModel):
    series_id: str
    season: int
    title: str
    overview: str = ""
    poster_path: str | None = None
    episodes: list[Episode] = Field(default_factory=list)


class SubtitleCandidate(BaseModel):
    source: str
    external_id: str
    download_url: SecretStr
    format: str
    delay_ms: int = 0


class ProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool = False, details: dict | None = None):
        self.code = code
        self.retryable = retryable
        self.details = details or {}
        # No server response, URL, credentials, paths or raw exception in public messages.
        super().__init__(code)
