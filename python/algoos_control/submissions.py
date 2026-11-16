"""Submission intake, lookup and cancellation."""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
from algoos_protocol import normalize, utc_now
from psycopg.types.json import Jsonb

from .db import Database
from .outbox import add_event
from .problem import ImportedProblem
from .storage import ObjectStore, digest_bytes

MAX_SOURCE_BYTES = 256 * 1024
SUPPORTED_PROBLEMS_ERROR = "unknown problem_id"


def upsert_problem(database: Database, imported: ImportedProblem) -> None:
    from .normalization import normalize_common

    problem = imported.problem
    config = {
        "problem_id": problem.problem_id,
        "title": problem.title,
        "time_limit_ms": problem.time_limit_ms,
        "memory_limit_mib": problem.memory_limit_mib,
        "compare_mode": problem.compare_mode,
        "judge_mode": problem.judge_mode,
        "checker_hash": imported.checker_hash,
        "output_limit_bytes": problem.output_limit_bytes,
        "case_hashes": imported.case_hashes,
        "tests": [{"case_id": case.case_id, "input": case.input, "output": case.output}
                  for case in problem.tests],
        "out_bytes": {
            case.case_id: len(normalize_common((problem.config_path.parent / case.output)
                                               .read_bytes()))
            for case in problem.tests
        },
    }
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO problems(problem_id,title,time_limit_ms,memory_limit_mib,judge_mode,"
            "compare_mode,bundle_hash,checker_hash,config) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (problem_id) DO UPDATE SET title=EXCLUDED.title,"
            "time_limit_ms=EXCLUDED.time_limit_ms,memory_limit_mib=EXCLUDED.memory_limit_mib,"
            "judge_mode=EXCLUDED.judge_mode,compare_mode=EXCLUDED.compare_mode,"
            "bundle_hash=EXCLUDED.bundle_hash,checker_hash=EXCLUDED.checker_hash,"
            "config=EXCLUDED.config",
            (problem.problem_id, problem.title, problem.time_limit_ms, problem.memory_limit_mib,
             problem.judge_mode, problem.compare_mode, imported.bundle_hash,
             imported.checker_hash, Jsonb(config)))


def _problem_row(connection: psycopg.Connection, problem_id: str) -> dict:
    row = connection.execute(
        "SELECT problem_id, title, time_limit_ms, memory_limit_mib, judge_mode, compare_mode,"
        "bundle_hash, checker_hash, config FROM problems WHERE problem_id=%s",
        (problem_id,)).fetchone()
    if row is None:
        raise KeyError(SUPPORTED_PROBLEMS_ERROR)
    return {
        "problem_id": row[0], "title": row[1], "time_limit_ms": row[2],
        "memory_limit_mib": row[3], "judge_mode": row[4], "compare_mode": row[5],
        "bundle_hash": row[6], "checker_hash": row[7], "config": row[8],
    }


def submit(database: Database, store: ObjectStore, policy: dict, *, problem_id: str,
           source: bytes, user_id: str = "anonymous", language: str = "cpp17",
           evaluation_mode: str | None = None, strategy: str | None = None) -> UUID:
    if not 0 < len(source) <= MAX_SOURCE_BYTES:
        raise ValueError("invalid source length")
    try:
        source.decode("utf-8")
    except UnicodeError as error:
        raise ValueError("source must be UTF-8 text") from error
    digest = digest_bytes(source)
    file_id = uuid4()
    if not store.exists(store.source_key(digest)):
        store.put(store.source_key(digest), source)
    profile = "cpp_baseline_v1"
    compiler = policy["compiler_profiles"][profile]
    mode = evaluation_mode or policy["result"]["short_circuit"]["default_evaluation_mode"]
    if mode not in policy["modes"]["evaluation_mode"]:
        raise ValueError("unsupported evaluation_mode")
    selected_strategy = strategy or policy["scheduling"]["default_strategy"]
    if selected_strategy not in policy["scheduling"]["strategies"]:
        raise ValueError("unsupported scheduling strategy")

    with database.transaction() as connection:
        problem = _problem_row(connection, problem_id)
        row = connection.execute("SELECT config FROM problems WHERE problem_id=%s",
                                 (problem_id,)).fetchone()
        config = row[0]
        # The importer wrote per-case derived lengths next to case hashes.
        out_bytes = config.get("out_bytes")
        if not out_bytes:
            raise ValueError("problem is missing derived out_bytes; re-import it")
        plan = []
        for sequence, case in enumerate(config["tests"], 1):
            input_hash, answer_hash = config["case_hashes"][case["case_id"]]
            plan.append({
                "test_id": case["case_id"], "sequence": sequence,
                "judge_mode": problem["judge_mode"], "compare_mode": problem["compare_mode"],
                "input_hash": input_hash, "answer_hash": answer_hash,
                "checker_hash": problem["checker_hash"],
                "limits": {
                    "cpu_ms": problem["time_limit_ms"],
                    "wall_ms": problem["time_limit_ms"] + 1000,
                    "mem_bytes": problem["memory_limit_mib"] << 20,
                    "out_bytes": int(out_bytes[case["case_id"]]),
                },
            })
        connection.execute(
            "INSERT INTO submissions(file_id,problem_id,user_id,language,source_hash,status,"
            "evaluation_mode,short_circuit_window,scheduling_strategy) "
            "VALUES (%s,%s,%s,%s,%s,'QUEUED',%s,%s,%s)",
            (file_id, problem_id, user_id, language, digest, mode,
             policy["result"]["short_circuit"]["window"], selected_strategy))
        seq = connection.execute(
            "INSERT INTO compile_jobs(file_id, task) VALUES (%s,%s) RETURNING enqueue_seq",
            (file_id, Jsonb({}))).fetchone()[0]
        task = normalize({
            "ver": 1, "type": "compile_task", "msg_id": f"{file_id}:compile:1",
            "file_id": str(file_id), "correlation_id": str(file_id),
            "problem_id": problem_id, "source_hash": digest,
            "test_bundle_hash": problem["bundle_hash"], "compiler_profile": profile,
            "enqueue_seq": seq, "attempt": 1, "limits": compiler["limits"],
            "emitted_at": utc_now(),
        }, policy)
        connection.execute("UPDATE compile_jobs SET task=%s WHERE file_id=%s",
                           (Jsonb(task), file_id))
        add_event(connection, str(file_id), policy["transport"]["compile_task_subject"],
                  task, policy)
        connection.execute("UPDATE submissions SET status='QUEUED' WHERE file_id=%s",
                           (file_id,))
    return file_id


def lookup(database: Database, file_id: UUID) -> dict | None:
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT file_id,problem_id,user_id,language,source_hash,status,verdict,reason,"
            "compile_log,submitted_at,finalized_at,evaluation_mode,short_circuit_window "
            "FROM submissions WHERE file_id=%s", (file_id,)).fetchone()
        if row is None:
            return None
        cases = connection.execute(
            "SELECT test_id,sequence,target_node_id,state,verdict,reason,judge_mode,limits "
            "FROM submission_cases WHERE file_id=%s ORDER BY sequence", (file_id,)).fetchall()
        results = connection.execute(
            "SELECT test_id,result FROM eval_results WHERE file_id=%s ORDER BY sequence",
            (file_id,)).fetchall()
        events = connection.execute(
            "SELECT reason FROM cancel_events WHERE file_id=%s ORDER BY event_id DESC LIMIT 1",
            (file_id,)).fetchone()
    result_map = {row[0]: row[1] for row in results}
    per_test = []
    for case in cases:
        payload = result_map.get(case[0], {})
        per_test.append({
            "test_id": case[0], "sequence": case[1],
            "node_id": payload.get("node_id") or case[2],
            "state": case[3], "judge_mode": case[6],
            "verdict": case[4] or payload.get("verdict"),
            "reason": case[5] or payload.get("reason"),
            "cpu_ms": payload.get("cpu_ms"), "wall_ms": payload.get("wall_ms"),
            "mem_bytes": payload.get("mem_bytes"), "memory_source": payload.get("memory_source"),
            "out_bytes": payload.get("out_bytes"), "source": payload.get("source"),
            "short_circuited": payload.get("short_circuited", False),
            "cache_hit": payload.get("cache_hit", False),
            "limits": case[7],
        })
    return {
        "ver": 1,
        "file_id": str(row[0]), "problem_id": row[1], "user_id": row[2], "language": row[3],
        "source_hash": row[4], "status": row[5], "verdict": row[6], "reason": row[7],
        "compile_log": row[8], "submitted_at": row[9].isoformat() if row[9] else None,
        "finalized_at": row[10].isoformat() if row[10] else None,
        "evaluation_mode": row[11], "short_circuit_window": row[12],
        "cancel_reason": events[0] if events else None,
        "per_test": per_test,
    }


def publish_cancel(connection: psycopg.Connection, policy: dict, *, file_id: str,
                   origin: str, scope: str = "submission", reason: str,
                   sequence_boundary: int | None = None,
                   judge_id: str | None = None) -> int:
    """Record a cancel event and enqueue its broadcast.  Returns generation."""
    generation = connection.execute(
        "SELECT cancel_generation FROM submissions WHERE file_id=%s FOR UPDATE",
        (file_id,)).fetchone()[0] + 1
    connection.execute("UPDATE submissions SET cancel_generation=%s WHERE file_id=%s",
                       (generation, file_id))
    connection.execute(
        "INSERT INTO cancel_events(file_id,judge_id,scope,sequence_boundary,cancel_generation,"
        "reason,origin) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (file_id, judge_id, scope, sequence_boundary, generation, reason, origin))
    timestamp = utc_now()
    message = normalize({
        "ver": 1, "type": "cancel", "msg_id": f"{file_id}:cancel:{generation}",
        "file_id": file_id, "correlation_id": file_id, "problem_id": None,
        "attempt": 1, "emitted_at": timestamp, "scope": scope, "reason": reason,
        "judge_id": judge_id, "sequence_boundary": sequence_boundary,
        "cancel_generation": generation, "origin": origin, "ts": timestamp,
    }, policy)
    add_event(connection, file_id, policy["transport"]["cancel_subject"], message, policy)
    if sequence_boundary is not None:
        connection.execute(
            "UPDATE submission_cases SET state='CANCELLED', verdict='CANCELLED', reason=%s "
            "WHERE file_id=%s AND state='READY' AND sequence > %s",
            (reason, file_id, sequence_boundary))
    else:
        connection.execute(
            "UPDATE submission_cases SET state='CANCELLED', verdict='CANCELLED', reason=%s "
            "WHERE file_id=%s AND state='READY'", (reason, file_id))
    return generation


def cancel_user(database: Database, policy: dict, file_id: UUID, *, origin: str) -> int:
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT status FROM submissions WHERE file_id=%s FOR UPDATE", (file_id,)).fetchone()
        if row is None:
            raise KeyError("unknown submission")
        if row[0] in ("FINAL", "CE"):
            raise ValueError("submission is already final")
        generation = publish_cancel(connection, policy, file_id=str(file_id), origin=origin,
                                    scope="submission", reason="USER_CANCELLED")
        connection.execute(
            "UPDATE submissions SET status='CANCELLED', verdict='CANCELLED', "
            "reason='USER_CANCELLED', finalized_at=now() WHERE file_id=%s", (file_id,))
    return generation
