"""N3.3 control-side compile dispatch and result ingestion."""
from datetime import datetime, timezone
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from protocol.codec_v1 import dumps, normalize
from scripts.eval_scheduler import round_robin


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_event(db, file_id, subject, payload, policy):
    payload = normalize(payload, policy)
    db.execute(
        "INSERT INTO algoos_outbox(msg_id,file_id,subject,payload) VALUES (%s,%s,%s,%s)",
        (payload["msg_id"], file_id, subject, Jsonb(payload)),
    )


def enqueue_compile(pg, file_id, problem, bundle_hash, node_id, policy, *, eval_nodes=None):
    # Freeze case order and limits at enqueue time, not at result arrival.
    if problem.compare_mode != "common" or not problem.tests:
        raise ValueError("expected a nonempty common problem")
    nodes = (node_id,) if eval_nodes is None else tuple(eval_nodes)
    eval_plan = [
        dict(test_id=case.case_id, sequence=index, target_node_id=round_robin(nodes, index),
             limits=dict(cpu_ms=problem.time_limit_ms, wall_ms=problem.time_limit_ms,
                         mem_bytes=problem.memory_limit_mib << 20, out_bytes=case.out_bytes))
        for index, case in enumerate(problem.tests, 1)
    ]
    if len({case["test_id"] for case in eval_plan}) != len(eval_plan):
        raise ValueError("duplicate test IDs")
    with psycopg.connect(**pg) as db:
        row = db.execute(
            "SELECT problem_id,source_hash,status FROM algoos_submissions WHERE file_id=%s FOR UPDATE",
            (file_id,),
        ).fetchone()
        if row is None or row[0] != problem.problem_id or row[2] != "RECEIVED":
            raise ValueError("submission is not ready for compile enqueue")
        seq = db.execute(
            "INSERT INTO algoos_compile_jobs(file_id,eval_plan) VALUES (%s,%s) RETURNING enqueue_seq",
            (file_id, Jsonb(eval_plan)),
        ).fetchone()[0]
        profile = "cpp_baseline_v1"
        task = normalize(dict(
            ver=1, type="compile_task", msg_id=f"{file_id}:compile:1", file_id=str(file_id),
            correlation_id=str(file_id), problem_id=row[0], emitted_at=utc_now(),
            source_hash=row[1], test_bundle_hash=bundle_hash, compiler_profile=profile,
            enqueue_seq=seq, attempt=1, limits=policy["compiler_profiles"][profile]["limits"],
        ), policy)
        db.execute("UPDATE algoos_compile_jobs SET task=%s WHERE file_id=%s", (Jsonb(task), file_id))
        add_event(db, file_id, policy["transport"]["compile_task_subject"], task, policy)
        db.execute("UPDATE algoos_submissions SET status='QUEUED' WHERE file_id=%s", (file_id,))
    return task


async def publish_pending(pg, js, policy):
    # Single local publisher. A crash between publish and marking may cause a retry;
    # stable message IDs allow broker deduplication within its configured window.
    with psycopg.connect(**pg) as db:
        rows = db.execute("SELECT event_id,subject,payload FROM algoos_outbox "
                          "WHERE published_at IS NULL ORDER BY event_id").fetchall()
    for event_id, subject, payload in rows:
        await js.publish(subject, dumps(payload, policy).encode(),
                         headers={"Nats-Msg-Id": payload["msg_id"]}, timeout=3)
        with psycopg.connect(**pg) as db:
            db.execute("UPDATE algoos_outbox SET published_at=now() WHERE event_id=%s", (event_id,))


def save_compile_result(pg, result, policy):
    result = normalize(result, policy)
    if result["type"] != "eval_result" or result["stage"] != "compile":
        raise ValueError("expected compile-stage result")
    file_id = UUID(result["file_id"])
    with psycopg.connect(**pg) as db:
        row = db.execute("SELECT task,eval_plan,result FROM algoos_compile_jobs WHERE file_id=%s FOR UPDATE",
                         (file_id,)).fetchone()
        if row is None:
            raise ValueError("unknown compile job")
        task, plan, prior = row
        for key in ("file_id", "problem_id", "correlation_id", "attempt",
                    "source_hash", "test_bundle_hash", "compiler_profile", "enqueue_seq"):
            if result.get(key) != task[key]:
                raise ValueError(f"compile result field mismatch: {key}")
        if prior is not None:
            if prior != result:
                raise ValueError("conflicting compile result")
            return
        compiled = result["status"] == "COMPILED"
        if compiled:
            for case in plan:
                judge_id = f"{file_id}:{case['test_id']}:1"
                event = dict(
                    ver=1, type="eval_task", msg_id=f"{judge_id}:eval", judge_id=judge_id,
                    file_id=str(file_id), correlation_id=str(file_id), problem_id=task["problem_id"],
                    emitted_at=utc_now(), enqueue_seq=task["enqueue_seq"], attempt=1,
                    bin_hash=result["bin_hash"], test_bundle_hash=task["test_bundle_hash"],
                    judge_mode="normal", compare_mode="common", evaluation_mode="all_cases", **case,
                )
                subject = policy["transport"]["eval_task_subject_template"].format(
                    target_node_id=case["target_node_id"])
                add_event(db, file_id, subject, event, policy)
        db.execute("UPDATE algoos_compile_jobs SET result=%s WHERE file_id=%s", (Jsonb(result), file_id))
        db.execute("UPDATE algoos_submissions SET status=%s WHERE file_id=%s",
                   ("COMPILED" if compiled else result["verdict"], file_id))
