"""Rebuildable, content-addressed bytes; durable bounded provider response cache."""

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

from reeldock.models import ProviderCache


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Cache:
    def __init__(self, db, directory: Path):
        self.db, self.directory = db, directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def put(self, content: bytes) -> str:
        key = sha256(content)
        target = self.directory / key
        if self.get(key) is None:
            fd, temporary = tempfile.mkstemp(dir=self.directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return key

    def get(self, key: str | None) -> bytes | None:
        if not key or not re.fullmatch("[0-9a-f]{64}", key):
            return None
        try:
            body = (self.directory / key).read_bytes()
            return body if sha256(body) == key else None
        except FileNotFoundError:
            return None

    @staticmethod
    def response_key(namespace, parameters) -> str:
        return sha256(json.dumps([namespace, parameters], sort_keys=True).encode())

    def response(self, namespace, parameters) -> dict | None:
        key = self.response_key(namespace, parameters)
        with self.db.sessions.begin() as session:
            row = session.get(ProviderCache, key)
            return row.body if row and row.expires_at > time.time() else None

    def save_response(self, namespace, parameters, body: dict, ttl=86400):
        with self.db.sessions.begin() as session:
            session.merge(
                ProviderCache(
                    key=self.response_key(namespace, parameters),
                    body=body,
                    expires_at=time.time() + ttl,
                )
            )
