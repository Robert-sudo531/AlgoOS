"""Step 4 end-to-end evidence: special judge, cancel, ordered short circuit.

Runs a real local cluster (Docker PostgreSQL/Redis/NATS + moto S3 + native
workers + Python agents) and writes ``artifacts/system/refactor_step4_<id>/``.
Trusted fixtures only; production isolation is explicitly out of scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from algoos_testing.cluster import ROOT, Cluster

PROBLEMS = ("a_plus_b", "special_pair", "short_circuit_probe")


def require(flag: bool, detail: str) -> None:
    if not flag:
        raise RuntimeError(detail)


def source(name: str) -> str:
    return (ROOT / "examples" / name).read_text(encoding="utf-8")


def submit_and_wait(cluster: Cluster, problem_id: str, example: str, *,
                    evaluation_mode: str | None = None, strategy: str | None = None,
                    timeout: float = 90.0) -> dict:
    file_id = cluster.submit(problem_id, source(example),
                             evaluation_mode=evaluation_mode, strategy=strategy)
    return cluster.wait_terminal(file_id, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="AI")
    args = parser.parse_args()
    evidence = ROOT / "artifacts" / "system" / f"refactor_step4_{uuid4().hex[:12]}"
    evidence.mkdir(parents=True)
    manifest: dict = {
        "executed_by": args.actor,
        "scope": "local trusted fixtures; special checker contract exercised; "
                 "no production sandbox validation",
        "cases": [],
        "status": "FAILED",
        "exit_code": 1,
    }
    started = time.monotonic()
    cluster: Cluster | None = None
    try:
        cluster = Cluster(problems=PROBLEMS, strategy="fcfs", log_dir=evidence / "logs")
        cluster.start()
        manifest["service_versions"] = {
            "postgres": cluster.query("SHOW server_version")[0][0],
            "nodes": cluster.list_nodes(),
        }

        # 1. Special checker returns 0 -> AC.
        view = submit_and_wait(cluster, "special_pair", "special_correct.cpp",
                               evaluation_mode="all_cases")
        require(view["status"] == "FINAL" and view["verdict"] == "AC",
                f"special AC failed: {view}")
        require(all(point["source"] == "special" for point in view["per_test"]),
                "special AC did not go through the special relay")
        require(all(point["verdict"] == "AC" for point in view["per_test"]),
                f"special AC points: {view['per_test']}")
        manifest["cases"].append({"case": "special_checker_exit_0", "expected": "AC",
                                  "view": view})

        # 2. Special checker returns 1 -> WA (ordered short circuit may cancel peers).
        view = submit_and_wait(cluster, "special_pair", "special_wrong.cpp")
        require(view["status"] == "FINAL" and view["verdict"] == "WA",
                f"special WA failed: {view}")
        wa_points = [point for point in view["per_test"]
                     if point["verdict"] == "WA" and point["reason"] == "SPECIAL_WA"]
        require(wa_points and wa_points[0]["source"] == "special",
                f"special WA points: {view['per_test']}")
        manifest["cases"].append({"case": "special_checker_exit_1", "expected": "WA",
                                  "view": view})

        # 3. Special checker returns 2 -> SE.
        view = submit_and_wait(cluster, "special_pair", "special_checker_error.cpp",
                               evaluation_mode="all_cases")
        require(view["status"] == "FINAL" and view["verdict"] == "SE",
                f"special checker error failed: {view}")
        require(all(point["verdict"] == "SE" and point["reason"] == "SYSTEM_ERROR"
                    for point in view["per_test"]),
                f"special SE points: {view['per_test']}")
        manifest["cases"].append({"case": "special_checker_exit_2", "expected": "SE",
                                  "view": view})

        # 4. Special checker exceeds its wall budget -> SE.
        view = submit_and_wait(cluster, "special_pair", "special_checker_timeout.cpp",
                               evaluation_mode="all_cases", timeout=120)
        require(view["status"] == "FINAL" and view["verdict"] == "SE",
                f"special checker timeout failed: {view}")
        manifest["cases"].append({"case": "special_checker_timeout", "expected": "SE",
                                  "view": view})

        # 5. User cancel while a normal case is in flight.
        file_id = cluster.submit("a_plus_b", source("sleepy.cpp"),
                                 evaluation_mode="all_cases", strategy="fcfs")

        def dispatched(view: dict) -> bool:
            return any(point["state"] == "DISPATCHED" for point in view["per_test"])

        cluster.wait_for(file_id, dispatched, timeout=30, label="in-flight cancel")
        before = cluster.view(file_id)
        require(before["status"] == "EVALUATING", f"unexpected pre-cancel state: {before}")
        response = cluster.cancel(file_id)
        view = cluster.wait_terminal(file_id, timeout=30)
        require(view["status"] == "CANCELLED" and view["verdict"] == "CANCELLED",
                f"user cancel failed: {view}")
        require(view["reason"] == "USER_CANCELLED",
                f"user cancel reason: {view['reason']}")
        require(all(point["verdict"] == "CANCELLED" for point in view["per_test"]),
                f"user cancel points: {view['per_test']}")
        cancel_rows = cluster.query(
            "SELECT reason, scope, cancel_generation FROM cancel_events WHERE file_id=%s",
            (file_id,))
        require(cancel_rows and cancel_rows[-1][0] == "USER_CANCELLED",
                f"cancel event missing: {cancel_rows}")
        manifest["cases"].append({"case": "user_cancel", "expected": "CANCELLED",
                                  "cancel_response": response, "view": view,
                                  "cancel_events": cancel_rows})

        # 6. Ordered short circuit: failure at sequence 2 must not kill sequence 1.
        file_id = cluster.submit("short_circuit_probe", source("short_circuit_probe.cpp"),
                                 evaluation_mode="ordered_short_circuit", strategy="fcfs")
        view = cluster.wait_terminal(file_id, timeout=60)
        points = {point["sequence"]: point for point in view["per_test"]}
        require(view["status"] == "FINAL" and view["verdict"] == "WA",
                f"short circuit final state: {view}")
        require(points[1]["verdict"] == "AC",
                f"lower sequence was disturbed by the short circuit: {points[1]}")
        require(points[2]["verdict"] == "WA" and points[2]["reason"] == "ANSWER_MISMATCH",
                f"boundary case verdict: {points[2]}")
        for sequence in range(3, 7):
            require(points[sequence]["verdict"] == "CANCELLED"
                    and points[sequence]["reason"] == "SHORT_CIRCUITED",
                    f"post-boundary case {sequence}: {points[sequence]}")
        cancel_rows = cluster.query(
            "SELECT reason, sequence_boundary, scope FROM cancel_events WHERE file_id=%s",
            (file_id,))
        require(cancel_rows and cancel_rows[0][0] == "SHORT_CIRCUITED"
                and cancel_rows[0][1] == 2 and cancel_rows[0][2] == "submission",
                f"short-circuit cancel event: {cancel_rows}")
        result_rows = cluster.query(
            "SELECT sequence, result->>'verdict', result->>'reason' FROM eval_results "
            "WHERE file_id=%s ORDER BY sequence", (file_id,))
        manifest["cases"].append({"case": "ordered_short_circuit", "expected": "WA",
                                  "view": view, "cancel_events": cancel_rows,
                                  "eval_results": result_rows})

        manifest["duration_s"] = round(time.monotonic() - started, 2)
        manifest.update(status="PASSED", exit_code=0)
        cluster.dump_logs(evidence / "logs")
        for name, path in sorted((ROOT / "examples").glob("*.cpp")):
            manifest.setdefault("example_sha256", {})[name] = hashlib.sha256(
                path.read_bytes()).hexdigest()
        print(f"[通过] 第 4 步端到端：特判 AC/WA/SE/超时、用户取消、有序短路")
    except Exception as error:
        manifest["error"] = str(error)
        print(f"[失败] {error}", flush=True)
        if cluster is not None:
            cluster.dump_logs(evidence / "logs")
    finally:
        if cluster is not None:
            cluster.stop()
    (evidence / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"运行证据：{evidence.relative_to(ROOT)}")
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
