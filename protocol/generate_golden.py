"""Generate synthetic v1 golden messages and verify them through the codec.

Run from the repository root: ``uv run python protocol/generate_golden.py``.
The messages are format examples only: hashes are placeholders, timings are
synthetic and the messages do not represent a real job.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from algoos_protocol import dumps, load_policy, loads, normalize  # noqa: E402

GOLDEN = ROOT / "protocol" / "golden"
FIXTURES = ROOT / "protocol" / "fixtures"
POLICY = load_policy(ROOT / "protocol" / "policy_v1.json")
A = "sha256:" + "a" * 64
B = "sha256:" + "b" * 64
C = "sha256:" + "c" * 64
D = "sha256:" + "d" * 64
E = "sha256:" + "e" * 64
TS = "2026-11-16T08:00:00Z"


def base(kind: str, message_id: str, file_id: str = "sub-0001") -> dict:
    return {
        "ver": 1, "type": kind, "msg_id": message_id, "file_id": file_id,
        "correlation_id": file_id, "problem_id": "a_plus_b", "attempt": 1,
        "emitted_at": TS,
    }


def compile_task() -> dict:
    message = base("compile_task", "sub-0001:compile:1", "sub-0001")
    message.update({
        "source_hash": A, "test_bundle_hash": B, "compiler_profile": "cpp_baseline_v1",
        "enqueue_seq": 1, "limits": POLICY["compiler_profiles"]["cpp_baseline_v1"]["limits"],
    })
    return message


def eval_task() -> dict:
    message = base("eval_task", "sub-0001:01:1:dispatch1", "sub-0001")
    message.update({
        "test_id": "01", "sequence": 1, "judge_id": "sub-0001:01:1", "enqueue_seq": 1,
        "bin_hash": C, "test_bundle_hash": B, "judge_mode": "normal",
        "compare_mode": "common", "evaluation_mode": "ordered_short_circuit",
        "target_node_id": "eval-01", "dispatch_generation": 1,
        "input_hash": D, "answer_hash": E,
        "limits": {"cpu_ms": 1000, "wall_ms": 2000, "mem_bytes": 268435456,
                   "out_bytes": 3},
    })
    return message


def eval_result() -> dict:
    message = base("eval_result", "sub-0001:01:1:runner:result", "sub-0001")
    message.update({
        "stage": "eval", "status": "FINAL", "node_id": "eval-01", "source": "runner",
        "test_id": "01", "sequence": 1, "judge_id": "sub-0001:01:1", "verdict": "AC",
        "reason": "NONE", "cpu_ms": 120, "wall_ms": 130, "mem_bytes": 15420,
        "memory_source": "cgroup_peak", "out_bytes": 3, "exit_code": 0, "signal": 0,
        "wall_timeout": False, "short_circuited": False, "stderr_tail": "",
        "detail": "",
    })
    return message


def compile_success() -> dict:
    message = base("eval_result", "sub-0001:compile:1:result", "sub-0001")
    message.update({
        "stage": "compile", "status": "COMPILED", "node_id": "compile-01",
        "source": "compiler", "test_id": None, "sequence": None, "judge_id": None,
        "test_bundle_hash": B, "compiler_profile": "cpp_baseline_v1", "enqueue_seq": 1,
        "bin_hash": C, "build_key": D, "compiler_version": "g++ 16.2.1",
        "verdict": None, "reason": "NONE", "cpu_ms": 800, "wall_ms": 900,
        "mem_bytes": None, "memory_source": None, "out_bytes": None, "exit_code": None,
        "signal": None, "wall_timeout": False, "short_circuited": False,
        "stderr_tail": "", "detail": "",
    })
    return message


def compile_error() -> dict:
    message = base("eval_result", "sub-0002:compile:1:result", "sub-0002")
    message.update({
        "stage": "compile", "status": "FINAL", "node_id": "compile-01",
        "source": "compiler", "test_id": None, "sequence": None, "judge_id": None,
        "test_bundle_hash": B, "compiler_profile": "cpp_baseline_v1", "enqueue_seq": 2,
        "bin_hash": None, "build_key": None, "compiler_version": None, "verdict": "CE",
        "reason": "COMPILE_ERROR", "cpu_ms": 300, "wall_ms": 310, "mem_bytes": None,
        "memory_source": None, "out_bytes": None, "exit_code": None, "signal": None,
        "wall_timeout": False, "short_circuited": False,
        "stderr_tail": "error: expected ';' before '}' token", "detail": "",
    })
    return message


def special_task() -> dict:
    message = base("special_task", "sub-0003:05:1:special:1", "sub-0003")
    message.update({
        "test_id": "05", "sequence": 5, "judge_id": "sub-0003:05:1", "enqueue_seq": 3,
        "out_hash": D, "ans_hash": E, "checker_hash": A, "input_hash": B,
        "test_bundle_hash": C,
        "limits": {"cpu_ms": 2000, "wall_ms": 3000, "mem_bytes": 268435456,
                   "out_bytes": 4096},
        "reply_to": {"eval_node_id": "eval-01", "session_id": "eval-01-abcd1234",
                     "reply_topic": "algoos.special.result.eval-01",
                     "deadline_ms": 1_800_000_000_000},
        "fallback_topic": "algoos.fallback",
    })
    return message


def special_result() -> dict:
    message = base("special_result", "sub-0003:05:1:special:result", "sub-0003")
    message.update({
        "test_id": "05", "sequence": 5, "judge_id": "sub-0003:05:1",
        "target_eval_node": "eval-01", "session_id": "eval-01-abcd1234",
        "source": "special", "verdict": "WA", "reason": "SPECIAL_WA",
        "detail": "line 5: expected 42, got 41", "stderr_tail": "",
        "cpu_ms": 45, "wall_ms": 50, "mem_bytes": 8192, "memory_source": "cgroup_peak",
        "short_circuited": False,
    })
    return message


def cancel() -> dict:
    message = base("cancel", "sub-0004:cancel:1", "sub-0004")
    message.update({
        "scope": "submission", "reason": "SHORT_CIRCUITED", "sequence_boundary": 2,
        "judge_id": None, "cancel_generation": 1, "origin": "eval-01", "ts": TS,
    })
    return message


def fallback() -> dict:
    message = base("fallback", "sub-0003:05:1:fallback", "sub-0003")
    message.update({
        "test_id": "05", "sequence": 5, "judge_id": "sub-0003:05:1",
        "reply_to": {"eval_node_id": "eval-01", "session_id": "eval-01-abcd1234",
                     "reply_topic": "algoos.special.result.eval-01",
                     "deadline_ms": 1_800_000_000_000},
        "verdict": "SE", "reason": "SYSTEM_ERROR", "source": "special",
        "fallback_reason": "target_eval_node_unreachable", "detail": "", "stderr_tail": "",
    })
    return message


def heartbeat() -> dict:
    return {
        "node_id": "eval-01", "role": "eval", "version": "1.0.0", "slots_total": 4,
        "slots_used": 1, "load_score": 900, "cached_hashes": [A, B, C],
        "sent_at_ms": 1_800_000_000_000, "endpoint": "/run/algoos/eval.sock",
    }


GOLDEN_MESSAGES = {
    "compile_task": compile_task,
    "eval_task": eval_task,
    "eval_result": eval_result,
    "compile_success_result": compile_success,
    "compile_error_result": compile_error,
    "special_task": special_task,
    "special_result": special_result,
    "cancel": cancel,
    "fallback": fallback,
    "heartbeat": heartbeat,
}


def main() -> int:
    GOLDEN.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, builder in GOLDEN_MESSAGES.items():
        message = builder()
        if name == "heartbeat":
            body = json.dumps(message, ensure_ascii=False, indent=2) + "\n"
        else:
            normalized = normalize(message, POLICY)
            body = dumps(normalized, POLICY)
            body = json.dumps(json.loads(body), ensure_ascii=False, indent=2) + "\n"
        path = GOLDEN / f"{name}.json"
        path.write_text(body, encoding="utf-8")
        if name != "heartbeat":
            # Round-trip: dumps -> loads -> dumps must be byte-stable.
            once = dumps(normalize(json.loads(body), POLICY), POLICY)
            twice = dumps(loads(once, POLICY), POLICY)
            if once != twice:
                print(f"round-trip mismatch: {name}", file=sys.stderr)
                return 1
        print(f"wrote {path.relative_to(ROOT)}")
    # Invalid fixtures for negative tests.
    invalid = {
        "duplicate_key.json": '{"ver":1,"ver":2,"type":"cancel"}',
        "unknown_type.json": json.dumps({**cancel(), "type": "unknown"}),
        "bad_judge_id.json": json.dumps({**eval_task(), "judge_id": "wrong"}),
        "compile_with_signal.json": json.dumps({**compile_success(), "signal": 9}),
        "short_circuit_on_normal.json": json.dumps({**eval_result(),
                                                    "short_circuited": True,
                                                    "reason": "ANSWER_MISMATCH"}),
        "nan.json": '{"ver": NaN}',
    }
    for name, body in invalid.items():
        (FIXTURES / name).write_text(body + "\n", encoding="utf-8")
    print(f"wrote {len(invalid)} invalid fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
