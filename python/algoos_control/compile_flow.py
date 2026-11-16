"""Control-side handling of compile results.

Compilation is dispatched at submission intake (compile_task in the outbox).
This module consumes ``compile.result`` notifications, records the outcome
exactly once, and materialises the frozen evaluation plan as READY cases.
"""

from __future__ import annotations

import psycopg
from algoos_protocol import normalize
from psycopg.types.json import Jsonb

from .db import Database


def handle_compile_result(database: Database, policy: dict, payload: dict) -> bool:
    """Return True when a new result was committed, False for a duplicate."""
    result = normalize(payload, policy)
    if result["type"] != "eval_result" or result["stage"] != "compile":
        raise ValueError("expected a compile-stage result")
    file_id = result["file_id"]
    with database.transaction() as connection:
        duplicate = connection.execute(
            "SELECT 1 FROM processed_messages WHERE msg_id=%s", (result["msg_id"],)).fetchone()
        if duplicate:
            return False
        row = connection.execute(
            "SELECT task,result FROM compile_jobs WHERE file_id=%s FOR UPDATE",
            (file_id,)).fetchone()
        if row is None:
            raise ValueError("compile result for an unknown job")
        task, prior = row
        for key in ("file_id", "problem_id", "correlation_id", "attempt", "source_hash",
                    "test_bundle_hash", "compiler_profile", "enqueue_seq"):
            if result.get(key) != task[key]:
                raise ValueError(f"compile result field mismatch: {key}")
        if prior is not None:
            if prior != result:
                raise ValueError("conflicting duplicate compile result")
            return False
        submission = connection.execute(
            "SELECT status FROM submissions WHERE file_id=%s FOR UPDATE",
            (file_id,)).fetchone()
        if submission is None:
            raise ValueError("compile result for an unknown submission")
        if submission[0] == "CANCELLED":
            connection.execute("INSERT INTO processed_messages(msg_id,consumer) "
                               "VALUES (%s,'compile')", (result["msg_id"],))
            return True
        connection.execute("UPDATE compile_jobs SET result=%s WHERE file_id=%s",
                           (Jsonb(result), file_id))
        connection.execute("INSERT INTO processed_messages(msg_id,consumer) VALUES (%s,'compile')",
                           (result["msg_id"],))
        if result["status"] == "COMPILED":
            connection.execute("UPDATE submissions SET status='EVALUATING' WHERE file_id=%s",
                               (file_id,))
            _materialize_cases(connection, file_id, task)
        else:
            connection.execute(
                "UPDATE submissions SET status=%s, verdict=%s, reason=%s, compile_log=%s, "
                "finalized_at=now() WHERE file_id=%s",
                ("CE" if result["verdict"] == "CE" else "FINAL",
                 result["verdict"], result["reason"], result["stderr_tail"] or result["detail"],
                 file_id))
    return True


def _materialize_cases(connection: psycopg.Connection, file_id: str, task: dict) -> None:
    """Create READY rows for every planned case exactly once."""
    if connection.execute(
            "SELECT 1 FROM submission_cases WHERE file_id=%s LIMIT 1", (file_id,)).fetchone():
        return
    plan = connection.execute(
        "SELECT config FROM problems WHERE problem_id=%s", (task["problem_id"],)).fetchone()[0]
    out_bytes = plan["out_bytes"]
    # Special checkers may accept several valid outputs of different lengths;
    # the problem author can set a wider semantic output budget.
    author_limit = plan.get("output_limit_bytes")
    for sequence, case in enumerate(plan["tests"], 1):
        input_hash, answer_hash = plan["case_hashes"][case["case_id"]]
        judge_id = f"{file_id}:{case['case_id']}:1"
        connection.execute(
            "INSERT INTO submission_cases(file_id,test_id,sequence,attempt,judge_id,judge_mode,"
            "state,limits) VALUES (%s,%s,%s,1,%s,%s,'READY',%s)",
            (file_id, case["case_id"], sequence, judge_id, plan["judge_mode"],
             Jsonb({
                 "cpu_ms": plan["time_limit_ms"],
                 "wall_ms": plan["time_limit_ms"] + 1000,
                 "mem_bytes": plan["memory_limit_mib"] << 20,
                 "out_bytes": int(author_limit or out_bytes[case["case_id"]]),
                 "input_hash": input_hash,
                 "answer_hash": answer_hash,
                 "checker_hash": plan.get("checker_hash"),
             })))
