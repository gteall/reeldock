from __future__ import annotations

import base64
import datetime
import hashlib
import http.client
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


class CheckError(Exception):
    """Only public codes/messages: never include URLs, credentials or response bodies."""

    def __init__(self, code: str, message: str = "", **details):
        super().__init__(message or code)
        self.code = code
        self.details = details


def origin(url: str) -> tuple[str, str, int]:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise CheckError("invalid_url", "Use HTTP(S) without credentials in the URL")
    try:
        return p.scheme, p.hostname.lower(), p.port or (443 if p.scheme == "https" else 80)
    except ValueError:
        raise CheckError("invalid_url") from None


def basic_auth(username: str, password: str) -> str:
    if ":" in username or any(c in username + password for c in "\r\n"):
        raise CheckError("invalid_credentials")
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    redirects: int = 0


class Transport:
    def __init__(self, timeout: float = 15, redirect_hosts=(), use_env_proxy=True):
        self.timeout = timeout
        self.redirect_hosts = {h.strip().lower() for h in redirect_hosts if h.strip()}
        handlers = [NoRedirect()] if use_env_proxy else [urllib.request.ProxyHandler({}), NoRedirect()]
        self.opener = urllib.request.build_opener(*handlers)
        self.bytes_read = 0
        self.requests = 0
        self.redirects = 0

    def request(self, method: str, url: str, *, headers=None, body=None,
                statuses=(200,), max_bytes=1024 * 1024, expected_range=None) -> Response:
        origin(url)
        req_headers = {"Accept-Encoding": "identity", "User-Agent": "ReelDock-P0/0.1"}
        req_headers.update(headers or {})
        redirects = 0
        credentials_dropped = False
        while True:
            req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
            self.requests += 1
            try:
                try:
                    response = self.opener.open(req, timeout=self.timeout)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    status = response.status
                    resp_headers = {k.lower(): v for k, v in response.headers.items()}
                    if status in {301, 302, 303, 307, 308}:
                        if method not in {"GET", "HEAD"}:
                            raise CheckError("mutation_redirect_blocked")
                        if redirects >= 5 or not resp_headers.get("location"):
                            raise CheckError("redirect_limit")
                        target = urllib.parse.urljoin(url, resp_headers["location"])
                        old, new = origin(url), origin(target)
                        if old[0] == "https" and new[0] != "https":
                            raise CheckError("redirect_downgrade_blocked")
                        if new != old:
                            if new[1] not in self.redirect_hosts:
                                raise CheckError("redirect_host_not_allowed")
                            credentials_dropped = True
                        if credentials_dropped:
                            req_headers = {k: v for k, v in req_headers.items()
                                           if k.lower() not in {"authorization", "cookie", "proxy-authorization",
                                                                "if-match", "if-unmodified-since", "if-range"}}
                        url = target
                        redirects += 1
                        self.redirects += 1
                        continue
                    # Validate range/status before consuming even one byte of a video body.
                    if expected_range is not None:
                        start, count, total = expected_range
                        if status != 206:
                            raise CheckError("range_ignored" if status == 200 else "range_http_error",
                                             http_status=status)
                        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", resp_headers.get("content-range", ""))
                        if not match or tuple(map(int, match.groups())) != (start, start + count - 1, total):
                            raise CheckError("invalid_content_range")
                        if resp_headers.get("content-encoding", "identity").lower() != "identity":
                            raise CheckError("encoded_range")
                        if "content-length" in resp_headers and resp_headers["content-length"] != str(count):
                            raise CheckError("invalid_range_length")
                    if status not in statuses:
                        raise CheckError("http_status", http_status=status)
                    content = b""
                    if method != "HEAD":
                        length = resp_headers.get("content-length")
                        if length and (not length.isdigit() or int(length) > max_bytes):
                            raise CheckError("body_budget_exceeded")
                        chunks = []
                        remaining = max_bytes + 1
                        while remaining:
                            chunk = response.read(min(65536, remaining))
                            if not chunk:
                                break
                            chunks.append(chunk)
                            self.bytes_read += len(chunk)
                            remaining -= len(chunk)
                        content = b"".join(chunks)
                        if len(content) > max_bytes:
                            raise CheckError("body_budget_exceeded")
                    if expected_range is not None and len(content) != expected_range[1]:
                        raise CheckError("short_range")
                    return Response(status, resp_headers, content, redirects)
            except CheckError:
                raise
            except (socket.timeout, TimeoutError):
                raise CheckError("network_timeout") from None
            except urllib.error.URLError as exc:
                code = "network_timeout" if isinstance(exc.reason, (socket.timeout, TimeoutError)) else "network_error"
                raise CheckError(code) from None
            except (http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionError,
                    ssl.SSLError, OSError):
                raise CheckError("transport_error") from None


def sample_positions(size: int) -> tuple[int, ...]:
    if size < 0xF000:
        raise CheckError("media_too_small")
    return 4096, (2 * size) // 3, size // 3, size - 8192


def shooter_hash(size: int, read_range) -> str:
    hashes = []
    for offset in sample_positions(size):
        block = read_range(offset, 4096)
        if len(block) != 4096:
            raise CheckError("short_range")
        hashes.append(hashlib.md5(block, usedforsecurity=False).hexdigest())
    return ";".join(hashes)


def local_hash(path: Path) -> str:
    before = path.stat()
    with path.open("rb") as handle:
        def read(offset, count):
            handle.seek(offset)
            return handle.read(count)
        result = shooter_hash(before.st_size, read)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise CheckError("source_changed")
    return result


def load_env(path: Path | None) -> dict[str, str]:
    values = {}
    if path is not None:
        if not path.is_file():
            raise CheckError("env_file_not_found")
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"P0_[A-Z_]+", key.strip()):
                raise CheckError("invalid_env_file")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    # Real environment takes precedence; never eval or source a credentials file.
    values.update({k: v for k, v in os.environ.items() if k.startswith("P0_")})
    return values


@dataclass
class Report:
    mode: str
    checks: list[dict] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

    def add(self, name, status, evidence, **details):
        self.checks.append({"check": name, "status": status, "evidence": evidence, **details})

    def run(self, name, evidence, action):
        try:
            details = action() or {}
            self.add(name, "passed", evidence, **details)
            return details
        except CheckError as exc:
            self.add(name, "failed", evidence, code=exc.code, **exc.details)
        except Exception:
            # Do not leak urllib/subprocess exceptions containing private URLs or paths.
            self.add(name, "failed", evidence, code="unexpected_error")
        return None

    def document(self):
        counts = {state: sum(c["status"] == state for c in self.checks)
                  for state in ("passed", "failed", "unverified")}
        return {"schema_version": 1, "mode": self.mode, "started_at": self.started_at, "summary": counts,
                "p0_complete": False, "checks": self.checks}

    def exit_code(self):
        if any(c["status"] == "failed" for c in self.checks):
            return 1
        if any(c["status"] == "unverified" for c in self.checks):
            return 2
        return 0

    def write(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.document(), ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
