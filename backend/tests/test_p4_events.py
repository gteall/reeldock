import asyncio
import json
import time

import pytest

from reeldock.events import stream_events
from reeldock.models import AuthSession, Event


async def connected():
    return False


def payload(frame):
    return json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))


async def test_sse_replay_reconnect_database_refresh_and_logout(foundation):
    db = foundation[0]
    with db.sessions.begin() as session:
        session.add(AuthSession(token_hash="token", csrf_hash="csrf", expires_at=time.time() + 100))
        event = Event(code="p4_ready", details={"stage": "base"})
        session.add(event)
        session.flush()
        first = event.id
    stream = stream_events(db, "token", 0, connected, poll_seconds=0.001)
    assert "event: ready" in await anext(stream)
    received = []
    while not received or received[-1]["id"] < first:
        received.append(payload(await anext(stream)))
    await stream.aclose()
    with db.sessions.begin() as session:
        event = Event(code="completed_while_offline", details={})
        session.add(event)
        session.flush()
        last = event.id
    resumed = stream_events(db, "token", first, connected, poll_seconds=0.001)
    await anext(resumed)
    assert payload(await asyncio.wait_for(anext(resumed), 1))["id"] == last
    with db.sessions.begin() as session:
        session.delete(session.get(AuthSession, "token"))
    assert "event: auth_expired" in await anext(resumed)
    with pytest.raises(StopAsyncIteration):
        await anext(resumed)


async def test_sse_reset_future_cursor_and_disconnect(foundation):
    db = foundation[0]
    with db.sessions.begin() as session:
        session.add(AuthSession(token_hash="token", csrf_hash="csrf", expires_at=time.time() + 100))
    stream = stream_events(db, "token", 999999, connected, poll_seconds=0.001, heartbeat_seconds=0)
    await anext(stream)
    assert "event: reset" in await anext(stream)
    await stream.aclose()

    async def disconnected():
        return True

    stream = stream_events(db, "token", 0, disconnected)
    await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


def test_stream_auth_and_invalid_cursor(client):
    assert (
        client.get("/api/events/stream", headers={"Last-Event-ID": "bad\nvalue"}).status_code == 422
    )
    client.post("/api/auth/logout")
    assert client.get("/api/events/stream").status_code == 401
