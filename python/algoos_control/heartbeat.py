"""Node heartbeat registration and liveness expiry."""

from __future__ import annotations

from algoos_protocol.models import Heartbeat
from psycopg.types.json import Jsonb

from .db import Database


def record_heartbeat(database: Database, payload: dict) -> None:
    heartbeat = Heartbeat(**payload)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO eval_nodes(node_id,role,version,slots_total,slots_used,load_score,"
            "cached_hashes,status,last_heartbeat) VALUES (%s,%s,%s,%s,%s,%s,%s,'ALIVE',now()) "
            "ON CONFLICT (node_id) DO UPDATE SET role=EXCLUDED.role,"
            "version=EXCLUDED.version, slots_total=EXCLUDED.slots_total,"
            "slots_used=EXCLUDED.slots_used, load_score=EXCLUDED.load_score,"
            "cached_hashes=EXCLUDED.cached_hashes, status='ALIVE', last_heartbeat=now()",
            (heartbeat.node_id, heartbeat.role, heartbeat.version, heartbeat.slots_total,
             heartbeat.slots_used, heartbeat.load_score,
             Jsonb(heartbeat.cached_hashes)))


def mark_stale(database: Database, stale_seconds: float = 15.0) -> int:
    with database.transaction() as connection:
        rows = connection.execute(
            "UPDATE eval_nodes SET status='DEAD' WHERE status='ALIVE' "
            "AND last_heartbeat < now() - make_interval(secs => %s) RETURNING node_id",
            (stale_seconds,)).fetchall()
    return len(rows)
