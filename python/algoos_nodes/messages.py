"""Wire message builders for node agents."""

from __future__ import annotations

import time

from algoos_protocol import utc_now


def _base(kind: str, msg_id: str, file_id: str, problem_id: str, attempt: int = 1) -> dict:
    return {
        "ver": 1, "type": kind, "msg_id": msg_id, "file_id": file_id,
        "correlation_id": file_id, "problem_id": problem_id, "attempt": attempt,
        "emitted_at": utc_now(),
    }


def eval_result(task: dict, node_id: str, *, verdict: str, reason: str,
                cpu_ms: int | None = None, wall_ms: int | None = None,
                mem_bytes: int | None = None, memory_source: str | None = None,
                out_bytes: int | None = None, exit_code: int | None = None,
                signal: int | None = None, wall_timeout: bool = False,
                short_circuited: bool = False, stderr_tail: str = "",
                detail: str = "", source: str = "runner") -> dict:
    # Keep the relay's eval result distinct from the special_result message
    # (they share the same judge_id and would otherwise collide on the
    # JetStream duplicate key inside the shared stream).
    message = _base("eval_result", f"{task['judge_id']}:{source}:eval",
                    task["file_id"], task["problem_id"], task["attempt"])
    message.update({
        "stage": "eval", "status": "FINAL", "node_id": node_id, "source": source,
        "test_id": task["test_id"], "judge_id": task["judge_id"],
        "sequence": task["sequence"], "verdict": verdict, "reason": reason,
        "cpu_ms": cpu_ms, "wall_ms": wall_ms, "mem_bytes": mem_bytes,
        "memory_source": memory_source, "out_bytes": out_bytes,
        "exit_code": exit_code, "signal": signal, "wall_timeout": wall_timeout,
        "short_circuited": short_circuited, "stderr_tail": stderr_tail,
        "detail": detail,
    })
    return message


def cancelled_result(task: dict, node_id: str, reason: str) -> dict:
    return eval_result(task, node_id, verdict="CANCELLED", reason=reason,
                       short_circuited=reason == "SHORT_CIRCUITED")


def compile_result(task: dict, node_id: str, *, status: str, verdict: str | None,
                   reason: str, bin_hash: str | None = None, build_key: str | None = None,
                   compiler_version: str | None = None, stderr_tail: str = "",
                   detail: str = "", cpu_ms: int | None = None,
                   wall_ms: int | None = None) -> dict:
    message = _base("eval_result", f"{task['msg_id']}:result", task["file_id"],
                    task["problem_id"], task.get("attempt", 1))
    message.update({
        "stage": "compile", "status": status, "node_id": node_id, "source": "compiler",
        "test_id": None, "sequence": None, "judge_id": None,
        "problem_id": task["problem_id"], "test_bundle_hash": task["test_bundle_hash"],
        "source_hash": task["source_hash"],
        "compiler_profile": task["compiler_profile"], "enqueue_seq": task["enqueue_seq"],
        "bin_hash": bin_hash, "build_key": build_key,
        "compiler_version": compiler_version, "verdict": verdict, "reason": reason,
        "cpu_ms": cpu_ms, "wall_ms": wall_ms, "mem_bytes": None, "memory_source": None,
        "out_bytes": None, "exit_code": None, "signal": None, "wall_timeout": False,
        "short_circuited": False, "stderr_tail": stderr_tail, "detail": detail,
    })
    return message


def special_task(task: dict, *, out_hash: str, ans_hash: str, checker_hash: str,
                 input_hash: str | None, reply_topic: str, fallback_topic: str,
                 session_id: str, limits: dict, enqueue_seq: int,
                 dispatch_generation: int) -> dict:
    message = _base("special_task", f"{task['judge_id']}:special:{dispatch_generation}",
                    task["file_id"], task["problem_id"], task["attempt"])
    message.update({
        "test_id": task["test_id"], "judge_id": task["judge_id"],
        "sequence": task["sequence"], "out_hash": out_hash, "ans_hash": ans_hash,
        "checker_hash": checker_hash, "input_hash": input_hash,
        "test_bundle_hash": task.get("test_bundle_hash"),
        "limits": limits, "enqueue_seq": enqueue_seq,
        "reply_to": {
            "eval_node_id": task["target_node_id"],
            "session_id": session_id,
            "reply_topic": reply_topic,
            "deadline_ms": int(time.time() * 1000) + 30_000,
        },
        "fallback_topic": fallback_topic,
    })
    return message


def special_result(task: dict, *, target_node: str, session_id: str, verdict: str,
                   reason: str, detail: str = "", stderr_tail: str = "",
                   cpu_ms: int | None = None, wall_ms: int | None = None,
                   mem_bytes: int | None = None, memory_source: str | None = None,
                   short_circuited: bool = False) -> dict:
    message = _base("special_result", f"{task['judge_id']}:special:result",
                    task["file_id"], task["problem_id"], task["attempt"])
    message.update({
        "test_id": task["test_id"], "judge_id": task["judge_id"],
        "sequence": task["sequence"], "target_eval_node": target_node,
        "session_id": session_id, "source": "special", "verdict": verdict,
        "reason": reason, "detail": detail, "stderr_tail": stderr_tail,
        "cpu_ms": cpu_ms, "wall_ms": wall_ms, "mem_bytes": mem_bytes,
        "memory_source": memory_source, "short_circuited": short_circuited,
    })
    return message


def fallback(task: dict, reply_to: dict, *, verdict: str, reason: str,
             fallback_reason: str, detail: str = "", stderr_tail: str = "") -> dict:
    message = _base("fallback", f"{task['judge_id']}:fallback",
                    task["file_id"], task["problem_id"], task["attempt"])
    message.update({
        "test_id": task["test_id"], "judge_id": task["judge_id"],
        "sequence": task["sequence"], "reply_to": reply_to, "verdict": verdict,
        "reason": reason, "source": "special", "fallback_reason": fallback_reason,
        "detail": detail, "stderr_tail": stderr_tail,
    })
    return message
