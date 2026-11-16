"""Result aggregation, de-duplication and ordered short circuit."""

from __future__ import annotations

import psycopg
from algoos_protocol import normalize, utc_now
from psycopg.types.json import Jsonb

from .db import Database
from .state import recompute_status
from .submissions import publish_cancel


def _existing_cancel_boundary(connection: psycopg.Connection, file_id: str) -> int | None:
    row = connection.execute(
        "SELECT MIN(sequence_boundary) FROM cancel_events WHERE file_id=%s "
        "AND reason='SHORT_CIRCUITED'", (file_id,)).fetchone()
    return row[0] if row else None


def handle_eval_result(database: Database, policy: dict, payload: dict) -> bool:
    """Persist one eval result; return False for an exact duplicate."""
    result = normalize(payload, policy)
    if result["type"] != "eval_result" or result["stage"] != "eval":
        raise ValueError("expected an eval-stage result")
    file_id = result["file_id"]
    judge_id = result["judge_id"]
    with database.transaction() as connection:
        duplicate = connection.execute(
            "SELECT 1 FROM processed_messages WHERE msg_id=%s", (result["msg_id"],)).fetchone()
        if duplicate:
            return False
        case = connection.execute(
            "SELECT sequence, judge_mode, state FROM submission_cases WHERE judge_id=%s "
            "AND file_id=%s FOR UPDATE", (judge_id, file_id)).fetchone()
        if case is None:
            raise ValueError(f"result for an unknown case: {judge_id}")
        prior = connection.execute(
            "SELECT result FROM eval_results WHERE judge_id=%s", (judge_id,)).fetchone()
        if prior is not None:
            if prior[0] != result:
                raise ValueError(f"conflicting duplicate eval result: {judge_id}")
            connection.execute("INSERT INTO processed_messages(msg_id,consumer) "
                               "VALUES (%s,'eval')", (result["msg_id"],))
            return False
        sequence, judge_mode, _state = case
        if result["sequence"] != sequence:
            raise ValueError("result sequence does not match the frozen plan")
        if result["source"] == "special" and judge_mode != "special":
            raise ValueError("special result for a normal case")
        connection.execute(
            "INSERT INTO eval_results(judge_id,file_id,test_id,sequence,judge_mode,result) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (judge_id, file_id, result["test_id"], sequence, judge_mode, Jsonb(result)))
        connection.execute(
            "UPDATE submission_cases SET state=%s, verdict=%s, reason=%s WHERE judge_id=%s",
            ("CANCELLED" if result["verdict"] == "CANCELLED" else "DONE",
             result["verdict"], result["reason"], judge_id))
        connection.execute("INSERT INTO processed_messages(msg_id,consumer) VALUES (%s,'eval')",
                           (result["msg_id"],))

        submission = connection.execute(
            "SELECT status, evaluation_mode FROM submissions WHERE file_id=%s",
            (file_id,)).fetchone()
        if submission is None:
            raise ValueError("result for an unknown submission")
        status, mode = submission
        if status == "CANCELLED":
            pass
        elif mode == "ordered_short_circuit" and result["verdict"] not in ("AC", "CANCELLED"):
            boundary = _existing_cancel_boundary(connection, file_id)
            if boundary is None or sequence < boundary:
                publish_cancel(connection, policy, file_id=file_id, origin=result["node_id"],
                               scope="submission", reason="SHORT_CIRCUITED",
                               sequence_boundary=sequence)
        recompute_status(connection, file_id, policy)
        connection.execute(
            "INSERT INTO processed_messages(msg_id,consumer) VALUES (%s,'aggregate') "
            "ON CONFLICT (msg_id) DO NOTHING", (result["msg_id"] + ":agg",))
    return True


def fallback_to_eval_result(policy: dict, fallback: dict) -> dict:
    """Translate a fallback event into an eval result handled by the aggregator."""
    timestamp = utc_now()
    return normalize({
        "ver": 1, "type": "eval_result", "msg_id": fallback["msg_id"] + ":result",
        "file_id": fallback["file_id"], "correlation_id": fallback["file_id"],
        "problem_id": fallback["problem_id"], "test_id": fallback["test_id"],
        "judge_id": fallback["judge_id"], "sequence": fallback["sequence"],
        "attempt": fallback["attempt"], "emitted_at": timestamp,
        "stage": "eval", "status": "FINAL", "node_id": fallback["reply_to"]["eval_node_id"],
        "source": "fallback", "verdict": fallback["verdict"], "reason": fallback["reason"],
        "cpu_ms": None, "wall_ms": None, "mem_bytes": None, "memory_source": None,
        "out_bytes": None, "exit_code": None, "signal": None,
        "wall_timeout": False, "short_circuited": False,
        "stderr_tail": fallback.get("stderr_tail", ""),
        "detail": fallback.get("detail", "") or fallback.get("fallback_reason", ""),
    }, policy)
