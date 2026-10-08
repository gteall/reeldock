from __future__ import annotations

import hashlib
import json
import posixpath
import time
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .core import CheckError, Transport, basic_auth, origin, shooter_hash


def xml_root(data: bytes):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise CheckError("unsafe_xml")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise CheckError("invalid_xml") from None


def status_code(value: str | None):
    parts = (value or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        raise CheckError("invalid_dav_status")
    return int(parts[1])


@dataclass(frozen=True)
class Entry:
    path: str
    collection: bool
    size: int | None
    etag: str | None
    modified: str | None


class WebDAV:
    def __init__(self, url, username="", password="", transport=None):
        p = urllib.parse.urlsplit(url)
        origin(url)
        if p.query or p.fragment:
            raise CheckError("invalid_webdav_url")
        self.base = url.rstrip("/") + "/"
        self.base_path = urllib.parse.unquote(p.path).rstrip("/") + "/"
        self.transport = transport or Transport()
        self.headers = {"Authorization": basic_auth(username, password)} if username else {}

    def url(self, path: str):
        if not path.startswith("/") or any(part in {".", ".."} for part in path.split("/")) or "\x00" in path:
            raise CheckError("invalid_dav_path")
        return self.base + urllib.parse.quote(path.lstrip("/"), safe="/")

    def _entry_path(self, href: str):
        url = urllib.parse.urljoin(self.base, href)
        if origin(url) != origin(self.base):
            raise CheckError("dav_href_outside_root")
        decoded = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
        if decoded.rstrip("/") == self.base_path.rstrip("/"):
            return "/"
        if not decoded.startswith(self.base_path):
            raise CheckError("dav_href_outside_root")
        path = "/" + decoded[len(self.base_path):]
        self.url(path)
        return path

    def list(self, path: str, depth=1) -> list[Entry]:
        if depth not in {0, 1}:
            raise CheckError("unsupported_depth")
        body = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/><d:getetag/><d:getlastmodified/></d:prop></d:propfind>'
        r = self.transport.request("PROPFIND", self.url(path),
                                   headers={**self.headers, "Depth": str(depth), "Content-Type": "application/xml"},
                                   body=body, statuses=(207, 404))
        if r.status == 404:
            return []
        root = xml_root(r.body)
        if root.tag != "{DAV:}multistatus":
            raise CheckError("invalid_multistatus")
        entries = []
        for response in root.findall("{DAV:}response"):
            direct = response.find("{DAV:}status")
            if direct is not None and not 200 <= status_code(direct.text) < 300:
                raise CheckError("dav_resource_failure", http_status=status_code(direct.text))
            props = {}
            for propstat in response.findall("{DAV:}propstat"):
                code = status_code(propstat.findtext("{DAV:}status"))
                if not 200 <= code < 300 and code != 404:
                    raise CheckError("dav_property_failure", http_status=code)
                if 200 <= code < 300:
                    prop = propstat.find("{DAV:}prop")
                    if prop is not None:
                        props.update({node.tag: node for node in prop})
            if not props:
                raise CheckError("dav_no_successful_properties")
            resource = props.get("{DAV:}resourcetype")
            collection = resource is not None and resource.find("{DAV:}collection") is not None
            length = props.get("{DAV:}getcontentlength")
            try:
                size = int(length.text) if length is not None and length.text else None
                if size is not None and size < 0:
                    raise ValueError
            except ValueError:
                raise CheckError("invalid_dav_size") from None
            etag = props.get("{DAV:}getetag")
            modified = props.get("{DAV:}getlastmodified")
            href = response.findtext("{DAV:}href")
            if not href:
                raise CheckError("dav_href_missing")
            entry_path = self._entry_path(href)
            expected = path.rstrip("/") or "/"
            if depth == 0 and entry_path.rstrip("/") != expected.rstrip("/"):
                raise CheckError("unexpected_dav_entry")
            if depth == 1 and entry_path.rstrip("/") != expected.rstrip("/"):
                parent = posixpath.dirname(entry_path.rstrip("/")) or "/"
                if parent != expected:
                    raise CheckError("unexpected_dav_entry")
            entries.append(Entry(entry_path, collection, size,
                                 etag.text if etag is not None else None,
                                 modified.text if modified is not None else None))
        return entries

    def stat(self, path: str) -> Entry | None:
        entries = self.list(path, 0)
        if len(entries) > 1:
            raise CheckError("duplicate_dav_entry")
        return entries[0] if entries else None

    def read_small(self, path, limit=1024 * 1024):
        return self.transport.request("GET", self.url(path), headers=self.headers, max_bytes=limit).body

    def read_range(self, path, start, count, size, etag=None):
        if start < 0 or count <= 0 or start + count > size:
            raise CheckError("range_out_of_bounds")
        headers = {**self.headers, "Range": f"bytes={start}-{start + count - 1}"}
        if etag and not etag.startswith("W/"):
            headers["If-Match"] = etag
        return self.transport.request("GET", self.url(path), headers=headers, statuses=(206,),
                                      max_bytes=count, expected_range=(start, count, size)).body

    def fingerprint(self, path):
        before = self.stat(path)
        if before is None or before.collection or before.size is None:
            raise CheckError("invalid_media_stat")
        digest = shooter_hash(before.size, lambda start, count:
                              self.read_range(path, start, count, before.size, before.etag))
        after = self.stat(path)
        if after != before:
            raise CheckError("source_changed")
        return digest, before.size


class Sandbox:
    """The only mutation surface: a new, uniquely named directory, no DELETE API."""

    def __init__(self, dav: WebDAV, parent: str, run_id=None):
        self.dav = dav
        if not parent or not parent.strip("/"):
            raise CheckError("dedicated_test_parent_required")
        parent_entry = dav.stat(parent)
        if parent_entry is None or not parent_entry.collection:
            raise CheckError("test_parent_missing")
        token = run_id or uuid.uuid4().hex
        if not re_run_id(token):
            raise CheckError("invalid_run_id")
        self.root = parent.rstrip("/") + "/.reeldock-p0-" + token
        if dav.stat(self.root) is not None:
            raise CheckError("test_run_already_exists")
        self._mutate("MKCOL", self.root, statuses=(201,))

    def path(self, relative):
        if relative.startswith("/") or any(p in {"", ".", ".."} for p in relative.split("/")):
            raise CheckError("outside_test_sandbox")
        return self.root + "/" + relative

    def _mutate(self, method, path, *, headers=None, body=None, statuses=(201, 204)):
        if method not in {"MKCOL", "PUT", "MOVE"}:
            raise CheckError("mutation_method_not_allowed")
        if path != self.root and not path.startswith(self.root + "/"):
            raise CheckError("outside_test_sandbox")
        return self.dav.transport.request(method, self.dav.url(path), headers={**self.dav.headers, **(headers or {})},
                                          body=body, statuses=statuses)

    def mkdir(self, relative):
        self._mutate("MKCOL", self.path(relative), statuses=(201,))

    def put(self, relative, data):
        self._mutate("PUT", self.path(relative), headers={"If-None-Match": "*"}, body=data)
        actual = self.dav.read_small(self.path(relative), len(data))
        if hashlib.sha256(actual).digest() != hashlib.sha256(data).digest():
            raise CheckError("readback_hash_mismatch")

    def move(self, source, target):
        r = self._mutate("MOVE", self.path(source), headers={"Destination": self.dav.url(self.path(target)),
                                                            "Overwrite": "F"}, statuses=(201, 204, 207, 409, 412))
        if r.status == 207:
            root = xml_root(r.body)
            failures = []
            for response in root.findall("{DAV:}response"):
                code = status_code(response.findtext("{DAV:}status"))
                if not 200 <= code < 300:
                    failures.append(code)
            raise CheckError("move_multistatus", failure_statuses=failures)
        return r.status


def re_run_id(value):
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value)


def reconcile(dav: WebDAV, source: str, target: str, identity: bytes, expected: dict[str, bytes],
              attempts=3, delay=0.2):
    """Read-only reconciliation; never infer success just because a target exists."""
    result = "move_unknown"
    for attempt in range(attempts):
        src, dst = dav.stat(source), dav.stat(target)
        if src is None and dst is not None and dst.collection:
            try:
                if dav.read_small(target + "/identity.json") != identity:
                    return "move_unknown"
                listing = dav.list(target)
                files = {e.path[len(target.rstrip("/")) + 1:] for e in listing
                         if e.path.rstrip("/") != target.rstrip("/")}
                if files != {"identity.json", *expected}:
                    return "move_unknown"
                if all(dav.read_small(target + "/" + name, len(data)) == data for name, data in expected.items()):
                    return "moved_verified"
            except CheckError:
                result = "move_unknown"
        elif src is not None and dst is None:
            result = "source_only"
        elif src is None and dst is None:
            result = "both_missing"
        else:
            result = "move_unknown"
        if attempt + 1 < attempts:
            time.sleep(delay)
    return result


def write_checks(sandbox: Sandbox):
    name = "影坞 space #100% .nfo"
    data = "<movie><title>ReelDock P0 自建测试</title></movie>".encode()
    sandbox.put(name, data)
    entries = sandbox.dav.list(sandbox.root)
    if not any(e.path == sandbox.path(name) and e.size == len(data) for e in entries):
        raise CheckError("filename_roundtrip_failed")
    return {"sha256_verified": True, "unicode_space_hash_percent": True}


def move_checks(sandbox: Sandbox, journal):
    sandbox.mkdir("source")
    identity = json.dumps({"package_id": uuid.uuid4().hex}, sort_keys=True).encode()
    payload = b"ReelDock generated attachment\n"
    sandbox.put("source/identity.json", identity)
    sandbox.put("source/attachment.txt", payload)
    # Contains private DAV paths, so journal is local-only and chmod 600 by caller.
    journal({"source": sandbox.path("source"), "target": sandbox.path("moved"),
             "identity": identity.decode(), "files": {"attachment.txt": payload.decode()}})
    response = "received"
    try:
        status = sandbox.move("source", "moved")
        if status not in {201, 204}:
            raise CheckError("move_rejected", http_status=status)
    except CheckError as exc:
        if exc.code not in {"network_timeout", "transport_error"}:
            raise
        response = "ambiguous_transport"
    state = reconcile(sandbox.dav, sandbox.path("source"), sandbox.path("moved"),
                      identity, {"attachment.txt": payload})
    if state != "moved_verified":
        raise CheckError("move_not_verified", state=state)
    return {"state": state, "initial_response": response}


def conflict_checks(sandbox: Sandbox):
    for directory in ("conflict-source", "conflict-target"):
        sandbox.mkdir(directory)
        sandbox.put(directory + "/sentinel.txt", directory.encode())
    try:
        status = sandbox.move("conflict-source", "conflict-target")
    except CheckError as exc:
        if exc.code != "http_status" or exc.details.get("http_status") != 500:
            raise
        # Some OpenList drivers return 500 for an existing destination. Still require
        # both complete sentinel bodies, and expose the protocol deviation explicitly.
        status = 500
    if status not in {409, 412, 500}:
        raise CheckError("overwrite_protection_failed", http_status=status)
    for directory in ("conflict-source", "conflict-target"):
        if sandbox.dav.read_small(sandbox.path(directory + "/sentinel.txt")) != directory.encode():
            raise CheckError("conflict_changed_data")
    return {"http_status": status, "source_and_target_preserved": True,
            "standard_conflict_status": status in {409, 412}}
