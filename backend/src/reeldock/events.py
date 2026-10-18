"""Durable SSE replay. The database remains the source of truth."""

import asyncio
import json
import time

from sqlalchemy import func, select

from reeldock.models import AuthSession, Event


def event_view(row):
    return {
        "id": row.id,
        "task_id": row.task_id,
        "code": row.code,
        "details": row.details,
        "created_at": row.created_at,
    }


def frame(event, data, event_id=None):
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return (
        prefix
        + "event: "
        + event
        + "\ndata: "
        + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    )


async def stream_events(
    db, token_hash, after, disconnected, *, poll_seconds=0.5, heartbeat_seconds=15
):
    yield "retry: 1500\n" + frame("ready", {"cursor": after})
    heartbeat = time.monotonic()
    while not await disconnected():
        with db.sessions.begin() as session:
            auth = session.get(AuthSession, token_hash)
            expired = not auth or auth.expires_at <= time.time()
            latest = session.scalar(select(func.max(Event.id))) or 0
            if after > latest:
                after = latest
                reset = True
            else:
                reset = False
            rows = [
                event_view(row)
                for row in session.scalars(
                    select(Event).where(Event.id > after).order_by(Event.id).limit(200)
                )
            ]
        # Never keep a transaction open across network yields or sleeps.
        if expired:
            yield frame("auth_expired", {})
            return
        if reset:
            yield frame("reset", {"cursor": after})
        for row in rows:
            after = row["id"]
            yield frame("reeldock", row, after)
        if time.monotonic() - heartbeat >= heartbeat_seconds:
            yield ": keepalive\n\n"
            heartbeat = time.monotonic()
        await asyncio.sleep(poll_seconds)
