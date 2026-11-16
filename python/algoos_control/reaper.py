"""Dead-node reaper: re-dispatch cases stranded on a stale evaluation node.

The broker already redelivers unacked messages when an agent process dies while
its node is alive.  This module covers the other failure: the node itself is
gone, so its per-node durable consumer will never be served.  Stranded cases
are reset to READY with the same ``judge_id``; the scheduler then dispatches
them to a live node.  Late results from the old node remain idempotent by
``judge_id``.
"""

from __future__ import annotations

from .db import Database

REAP_GRACE_SECONDS = 2.0


def reap_once(database: Database, *, stale_seconds: float = 15.0,
              grace_seconds: float = REAP_GRACE_SECONDS) -> int:
    """Reset stranded cases; return the number of cases re-queued."""
    with database.transaction() as connection:
        alive = connection.execute(
            "SELECT node_id FROM eval_nodes WHERE role='eval' AND status='ALIVE' "
            "AND last_heartbeat > now() - make_interval(secs => %s)", (stale_seconds,)
        ).fetchall()
        if not alive:
            return 0
        rows = connection.execute(
            "SELECT sc.file_id, sc.test_id, sc.judge_id, sc.target_node_id "
            "FROM submission_cases sc "
            "JOIN submissions s ON s.file_id = sc.file_id "
            "LEFT JOIN eval_nodes n ON n.node_id = sc.target_node_id "
            "WHERE sc.state = 'DISPATCHED' "
            "AND s.status IN ('EVALUATING','PENDING_SPECIAL') "
            "AND sc.dispatched_at IS NOT NULL "
            "AND sc.dispatched_at < now() - make_interval(secs => %s) "
            "AND (n.node_id IS NULL OR n.status = 'DEAD' "
            "     OR n.last_heartbeat < now() - make_interval(secs => %s)) "
            "FOR UPDATE OF sc SKIP LOCKED",
            (grace_seconds, stale_seconds)).fetchall()
        for file_id, test_id, judge_id, _target in rows:
            connection.execute(
                "UPDATE submission_cases SET state='READY', target_node_id=NULL, "
                "dispatched_at=NULL WHERE file_id=%s AND test_id=%s",
                (file_id, test_id))
            connection.execute(
                "INSERT INTO processed_messages(msg_id, consumer) VALUES (%s,'reaper') "
                "ON CONFLICT (msg_id) DO NOTHING", (f"reap:{judge_id}",))
        return len(rows)
