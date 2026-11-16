"""Transactional outbox: persist an event first, publish it afterwards."""

from __future__ import annotations

from typing import Any

import psycopg
from algoos_protocol import normalize
from psycopg.types.json import Jsonb

from .broker import Broker
from .db import Database


def add_event(connection: psycopg.Connection, file_id: str, subject: str, payload: dict,
              policy: dict) -> str:
    normalized = normalize(payload, policy)
    connection.execute(
        "INSERT INTO outbox(msg_id, file_id, subject, payload) VALUES (%s,%s,%s,%s)",
        (normalized["msg_id"], file_id, subject, Jsonb(normalized)))
    return normalized["msg_id"]


def pending_events(database: Database, limit: int = 128) -> list[dict[str, Any]]:
    with database.transaction(row_factory=None) as connection:
        rows = connection.execute(
            "SELECT event_id, subject, payload FROM outbox "
            "WHERE published_at IS NULL ORDER BY event_id LIMIT %s", (limit,)).fetchall()
    return [{"event_id": row[0], "subject": row[1], "payload": row[2]} for row in rows]


async def publish_pending(database: Database, broker: Broker, policy: dict,
                          limit: int = 128) -> int:
    """Publish at most ``limit`` pending events; return the number published.

    A crash between publish and the ``published_at`` update can re-publish an
    event.  ``msg_id`` is stable, so JetStream de-duplicates within its
    duplicate window and consumers remain idempotent beyond it.
    """
    published = 0
    for event in pending_events(database, limit):
        await broker.publish(event["subject"], event["payload"], event["payload"]["msg_id"])
        with database.transaction() as connection:
            connection.execute("UPDATE outbox SET published_at=now() WHERE event_id=%s",
                               (event["event_id"],))
        published += 1
    return published
