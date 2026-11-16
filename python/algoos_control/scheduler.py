"""Eval-task dispatcher: windowed ordered short circuit + affinity routing."""

from __future__ import annotations

import time
from datetime import timedelta

from algoos_algorithms import NodeState, ReadyCase, assign
from algoos_protocol import normalize, utc_now

from .db import Database
from .outbox import add_event

ALIVE_WINDOW = timedelta(seconds=15)
DISPATCH_BATCH = 64


def _alive_nodes(connection) -> list[NodeState]:
    rows = connection.execute(
        "SELECT node_id, slots_total, slots_used, load_score, cached_hashes "
        "FROM eval_nodes WHERE role='eval' AND status='ALIVE' "
        "AND last_heartbeat > now() - %s ORDER BY node_id",
        (ALIVE_WINDOW,)).fetchall()
    nodes = []
    for node_id, total, used, load, cached in rows:
        hashes = cached if isinstance(cached, list) else []
        nodes.append(NodeState(node_id=node_id, slots=max(1, total), running=min(total, used),
                               load_score=float(load or 0.0),
                               cached_hashes=tuple(str(item) for item in hashes)))
    return nodes


def _estimate_ms(case: dict) -> float:
    limits = case["limits"]
    # Compilation is already done; one case costs its CPU budget plus checker
    # and transfer overhead.  It is an estimate, never the observed time.
    return max(500.0, float(limits["cpu_ms"]) + 500.0)


def dispatch_once(database: Database, policy: dict, *, origin: str = "control") -> int:
    """Publish at most ``DISPATCH_BATCH`` eval tasks; return the number sent."""
    dispatched = 0
    with database.transaction() as connection:
        submissions = connection.execute(
            "SELECT file_id, problem_id, scheduling_strategy, evaluation_mode, "
            "short_circuit_window FROM submissions "
            "WHERE status IN ('EVALUATING','PENDING_SPECIAL') ORDER BY submitted_at LIMIT 64 "
            "FOR UPDATE SKIP LOCKED").fetchall()
        nodes = _alive_nodes(connection)
        if not nodes:
            return 0
        for file_id, problem_id, strategy, mode, window in submissions:
            result_row = connection.execute(
                "SELECT result, task FROM compile_jobs WHERE file_id=%s", (file_id,)).fetchone()
            if not result_row or not result_row[0] or result_row[0].get("status") != "COMPILED":
                continue
            compile_result, compile_task = result_row
            problem = connection.execute(
                "SELECT bundle_hash, config FROM problems WHERE problem_id=%s",
                (problem_id,)).fetchone()
            if problem is None:
                continue
            bundle_hash, config = problem
            cases = connection.execute(
                "SELECT test_id, sequence, judge_id, state, judge_mode, limits, "
                "dispatch_generation FROM submission_cases WHERE file_id=%s "
                "ORDER BY sequence", (file_id,)).fetchall()
            if not cases:
                continue
            non_terminal = [case for case in cases if case[3] not in ("DONE", "CANCELLED")]
            if not non_terminal:
                continue
            lowest = min(case[1] for case in non_terminal)
            ready = [case for case in cases if case[3] == "READY"]
            if mode == "ordered_short_circuit":
                ready = [case for case in ready if case[1] <= lowest + max(1, window) - 1]
            elif mode == "all_cases":
                pass
            else:
                continue
            if not ready:
                continue
            remaining = ready
            now_ms = time.monotonic() * 1000
            working_nodes = [
                NodeState(node_id=node.node_id, slots=node.slots, running=node.running,
                          load_score=node.load_score, cached_hashes=node.cached_hashes)
                for node in nodes
            ]
            while remaining and any(node.free_slots for node in working_nodes):
                ready_models = [
                    ReadyCase(
                        file_id=str(file_id), test_id=case[0], sequence=case[1],
                        enqueue_seq=int(compile_task["enqueue_seq"]), attempt=1,
                        estimate_ms=_estimate_ms({"limits": case[5]}),
                        arrival_ms=0.0,
                        bin_hash=compile_result.get("bin_hash", ""),
                        bundle_hash=bundle_hash,
                    )
                    for case in remaining
                ]
                assignment = assign(ready_models, working_nodes, strategy or "cache_aware",
                                    now_ms=now_ms)
                case = next(case for case in remaining
                            if case[0] == assignment.case.test_id)
                case_limits = case[5]
                generation = case[6] + 1
                judge_id = f"{file_id}:{case[0]}:1"
                message = normalize({
                    "ver": 1, "type": "eval_task",
                    "msg_id": f"{judge_id}:d{generation}:eval",
                    "file_id": str(file_id), "correlation_id": str(file_id),
                    "problem_id": problem_id, "test_id": case[0], "sequence": case[1],
                    "judge_id": judge_id, "attempt": 1,
                    "enqueue_seq": int(compile_task["enqueue_seq"]),
                    "bin_hash": compile_result["bin_hash"],
                    "test_bundle_hash": bundle_hash,
                    "judge_mode": case[4], "compare_mode": config["compare_mode"],
                    "evaluation_mode": mode,
                    "target_node_id": assignment.node_id,
                    "dispatch_generation": generation,
                    "limits": {
                        "cpu_ms": case_limits["cpu_ms"], "wall_ms": case_limits["wall_ms"],
                        "mem_bytes": case_limits["mem_bytes"],
                        "out_bytes": case_limits["out_bytes"],
                    },
                    "input_hash": case_limits.get("input_hash"),
                    "answer_hash": case_limits.get("answer_hash"),
                    "checker_hash": case_limits.get("checker_hash"),
                    "emitted_at": utc_now(),
                }, policy)
                subject = policy["transport"]["eval_task_subject_template"].format(
                    target_node_id=assignment.node_id)
                add_event(connection, str(file_id), subject, message, policy)
                connection.execute(
                    "UPDATE submission_cases SET state='DISPATCHED', target_node_id=%s, "
                    "dispatch_generation=%s WHERE file_id=%s AND test_id=%s",
                    (assignment.node_id, generation, file_id, case[0]))
                remaining = [item for item in remaining if item[0] != case[0]]
                for index, node in enumerate(working_nodes):
                    if node.node_id == assignment.node_id:
                        working_nodes[index] = NodeState(
                            node_id=node.node_id, slots=node.slots, running=node.running + 1,
                            load_score=node.load_score + assignment.estimate_ms,
                            cached_hashes=node.cached_hashes)
                dispatched += 1
                if dispatched >= DISPATCH_BATCH:
                    break
            if remaining:
                continue
            has_special = connection.execute(
                "SELECT 1 FROM submission_cases WHERE file_id=%s AND judge_mode='special' "
                "AND state IN ('READY','DISPATCHED','PENDING_SPECIAL') LIMIT 1",
                (file_id,)).fetchone()
            if has_special:
                connection.execute(
                    "UPDATE submissions SET status='PENDING_SPECIAL' WHERE file_id=%s "
                    "AND status <> 'CANCELLED'", (file_id,))
            if dispatched >= DISPATCH_BATCH:
                break
    return dispatched
