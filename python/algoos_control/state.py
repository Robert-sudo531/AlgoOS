"""Submission state machine and final aggregation helpers."""

from __future__ import annotations

import psycopg

TERMINAL_CASE_STATES = {"DONE", "CANCELLED"}


def submission_cancelled_by_user(connection: psycopg.Connection, file_id: str) -> bool:
    row = connection.execute(
        "SELECT status FROM submissions WHERE file_id=%s", (file_id,)).fetchone()
    return row is not None and row[0] == "CANCELLED"


def first_non_ac(rows: list[dict]) -> tuple[str, str]:
    """Rows are ordered by sequence; AC only when every case is AC."""
    for row in rows:
        if row["verdict"] != "AC":
            return row["verdict"], row["reason"] or "NONE"
    return "AC", "NONE"


def recompute_status(connection: psycopg.Connection, file_id: str, policy: dict) -> str:
    """Recompute a submission status from its case rows before finalization."""
    submission = connection.execute(
        "SELECT status, verdict, reason, evaluation_mode, cancel_generation "
        "FROM submissions WHERE file_id=%s FOR UPDATE", (file_id,)).fetchone()
    if submission is None:
        raise ValueError("unknown submission")
    status, _verdict, _reason, _mode, _generation = submission
    cases = connection.execute(
        "SELECT test_id, sequence, state, judge_mode, verdict, reason FROM submission_cases "
        "WHERE file_id=%s ORDER BY sequence", (file_id,)).fetchall()
    last_cancel = connection.execute(
        "SELECT reason, sequence_boundary, scope FROM cancel_events WHERE file_id=%s "
        "ORDER BY event_id DESC LIMIT 1", (file_id,)).fetchone()
    if status == "CANCELLED" and last_cancel and last_cancel[0] == "USER_CANCELLED":
        return "CANCELLED"
    if not cases:
        return status
    if all(row[2] in TERMINAL_CASE_STATES for row in cases):
        rows = [{"sequence": row[1], "verdict": row[4], "reason": row[5]} for row in cases]
        final_verdict, final_reason = first_non_ac(rows)
        if last_cancel and last_cancel[0] == "USER_CANCELLED":
            final_verdict, final_reason = "CANCELLED", "USER_CANCELLED"
        connection.execute(
            "UPDATE submissions SET status=%s, verdict=%s, reason=%s, finalized_at=now() "
            "WHERE file_id=%s", ("CANCELLED" if final_verdict == "CANCELLED" else "FINAL",
                                 final_verdict, final_reason, file_id))
        return "CANCELLED" if final_verdict == "CANCELLED" else "FINAL"
    if any(row[3] == "special" and row[2] in ("DISPATCHED", "PENDING_SPECIAL") for row in cases):
        next_status = "PENDING_SPECIAL"
    elif status == "SHORT_CIRCUITED" or (last_cancel and last_cancel[0] == "SHORT_CIRCUITED"):
        next_status = "SHORT_CIRCUITED"
    else:
        next_status = "EVALUATING"
    connection.execute("UPDATE submissions SET status=%s WHERE file_id=%s", (next_status, file_id))
    return next_status
