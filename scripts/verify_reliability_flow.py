"""Verify cache, duplicate-result, restart, reaper and fallback flows.

All scenarios use the real local cluster (Docker PostgreSQL/Redis/NATS + MinIO
+ native workers + Python agents) and write
``artifacts/verification/<UTC timestamp>_reliability_flow/``.
Faults are injected by stopping agent and worker processes; this is a local
reliability test, not a production HA claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from uuid import uuid4

from algoos_protocol import load_policy
from algoos_testing.local_cluster import ROOT, LocalCluster
from psycopg.types.json import Jsonb

PROBLEMS = ("a_plus_b", "special_pair")


def require(flag: bool, detail: str) -> None:
    if not flag:
        raise RuntimeError(detail)


def source(name: str) -> str:
    return (ROOT / "examples" / name).read_text(encoding="utf-8")


def wait_result(cluster: LocalCluster, file_id: str, timeout: float = 90.0) -> dict:
    return cluster.wait_cases_terminal(file_id, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="AI")
    args = parser.parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_reliability_flow"
    evidence = ROOT / "artifacts" / "verification" / run_id
    if evidence.exists():
        evidence = evidence.with_name(f"{run_id}_{uuid4().hex[:6]}")
    evidence.mkdir(parents=True)
    manifest: dict = {
        "executed_by": args.actor,
        "scope": "local trusted fixtures; real MinIO S3 (Chainguard build); "
                 "no production isolation claim",
        "cases": [],
        "status": "FAILED",
        "exit_code": 1,
    }
    started = time.monotonic()
    cluster: LocalCluster | None = None
    try:
        cluster = LocalCluster(
            problems=PROBLEMS, strategy="cache_aware",
            log_dir=evidence / "logs",
            extra_env={
                "ALGOOS_STALE_SECONDS": "4",
                "ALGOOS_REAPER_INTERVAL": "1",
                "ALGOOS_EVAL_ACK_WAIT": "3",
                "ALGOOS_CONTROL_ACK_WAIT": "3",
            },
        )
        cluster.start()
        manifest["service_versions"] = {
            "postgres": cluster.query("SHOW server_version")[0][0],
            "s3_backend": cluster.s3_mode,
            "s3_image": cluster.s3_image or "moto",
            "nodes": cluster.list_nodes(),
        }

        # 1. Cache hit: warm the nodes, wait for the 2 s heartbeat to expose the
        # cached hashes, then verify a repeated run is served locally.  Without
        # the heartbeat wait the scheduler works from a stale cache snapshot and
        # the test becomes timing-dependent.
        def await_cache_visible(view: dict, timeout: float = 12.0) -> bool:
            row = cluster.query("SELECT result FROM compile_jobs WHERE file_id=%s",
                                (view["file_id"],))[0]
            bin_hash = (row[0] or {}).get("bin_hash")
            expected: dict[str, set[str]] = {}
            for point in view["per_test"]:
                node = point.get("node_id")
                if not node:
                    continue
                hashes = expected.setdefault(node, set())
                if bin_hash:
                    hashes.add(bin_hash)
                for key in ("input_hash", "answer_hash"):
                    value = (point.get("limits") or {}).get(key)
                    if value:
                        hashes.add(value)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if all(
                    hashes.issubset(set(
                        json.loads(cluster.redis_client.get(
                            f"algoos:heartbeat:{node}") or "{}").get(
                                "cached_hashes", [])))
                    for node, hashes in expected.items()
                ):
                    return True
                time.sleep(0.2)
            return False

        warmed = []
        third = None
        for index in range(6):
            file_id = cluster.submit("a_plus_b", source("a_plus_b.cpp"),
                                     evaluation_mode="all_cases")
            view = wait_result(cluster, file_id)
            require(view["status"] == "FINAL" and view["verdict"] == "AC",
                    f"cache warm run {index} failed: {view}")
            warmed.append(view)
            require(await_cache_visible(view),
                    f"cache hashes were not exposed by heartbeat after run {index}")
            hits = [point.get("cache_hit") for point in view["per_test"]]
            if hits and all(hits):
                third = view
                break
        require(third is not None,
                f"no fully cached run after {len(warmed)} attempts: "
                f"{[point.get('cache_hit') for point in warmed[-1]['per_test']]}")
        require(len(warmed) >= 2, "cache hit observed without a prior warm-up run")
        heartbeat_hashes = {
            node: json.loads(cluster.redis_client.get(f"algoos:heartbeat:{node}") or "{}")
            .get("cached_hashes", [])
            for node in ("eval-01", "eval-02")
        }
        require(all(heartbeat_hashes[node] for node in heartbeat_hashes),
                f"node heartbeat has no cached hashes: {heartbeat_hashes}")
        manifest["cases"].append({
            "case": "cache_hit_after_warmup", "expected": "cache_hit=true",
            "warmup_runs": len(warmed), "third_view": third,
            "heartbeat_cached_hashes": {
                node: len(values) for node, values in heartbeat_hashes.items()},
        })

        # 2. Duplicate and conflicting result injection: keep the first result.
        file_id = warmed[0]["file_id"]
        row = cluster.query(
            "SELECT judge_id, result FROM eval_results WHERE file_id=%s ORDER BY sequence LIMIT 1",
            (file_id,))[0]
        judge_id, original = row
        before_count = cluster.query(
            "SELECT count(*) FROM eval_results WHERE file_id=%s", (file_id,))[0][0]
        before_view = cluster.view(file_id)
        duplicate = dict(original)
        duplicate["msg_id"] = duplicate["msg_id"] + ":duplicate"
        cluster.publish("algoos.eval.result", duplicate)
        conflicting = dict(original)
        conflicting["msg_id"] = conflicting["msg_id"] + ":conflict"
        conflicting["verdict"] = "WA"
        conflicting["reason"] = "ANSWER_MISMATCH"
        cluster.publish("algoos.eval.result", conflicting)
        time.sleep(1.5)
        after_count = cluster.query(
            "SELECT count(*) FROM eval_results WHERE file_id=%s", (file_id,))[0][0]
        after_view = cluster.view(file_id)
        require(after_count == before_count,
                f"duplicate result changed row count: {before_count}->{after_count}")
        require(after_view["status"] == before_view["status"]
                and after_view["verdict"] == before_view["verdict"],
                "duplicate/conflicting result changed the final submission")
        manifest["cases"].append({
            "case": "duplicate_and_conflict_result", "expected": "first result kept",
            "judge_id": judge_id, "rows_before": before_count, "rows_after": after_count,
            "final": after_view["verdict"],
        })

        # 3. Agent restart: kill an eval agent while a case is unacked, restart it and
        #    let the per-node durable redeliver the same judge_id.
        file_id = cluster.submit("a_plus_b", source("sleepy.cpp"),
                                 evaluation_mode="all_cases", strategy="fcfs")

        in_flight = cluster.wait_for(
            file_id,
            lambda view: any(point["state"] == "DISPATCHED" for point in view["per_test"]),
            timeout=30, label="in-flight eval task")
        target_node = next(point["node_id"] for point in in_flight["per_test"]
                           if point["state"] == "DISPATCHED")
        cluster.stop_process(f"{target_node}-agent", force=True)
        time.sleep(1.0)
        cluster.restart_agent(target_node, "eval")
        view = wait_result(cluster, file_id, timeout=90)
        require(view["status"] == "FINAL", f"restart run did not finalize: {view}")
        duplicates = cluster.query(
            "SELECT judge_id, count(*) FROM eval_results WHERE file_id=%s "
            "GROUP BY judge_id HAVING count(*) > 1", (file_id,))
        require(not duplicates, f"restart produced duplicate results: {duplicates}")
        manifest["cases"].append({
            "case": "agent_restart_redelivery", "expected": "same judge_id once",
            "view": view, "duplicate_rows": duplicates,
        })

        # 4. Dead-node reaper: kill the eval-01 agent and native worker, then verify the
        #    stranded case is re-queued and executed on eval-02.
        file_id = cluster.submit("a_plus_b", source("sleepy.cpp"),
                                 evaluation_mode="all_cases", strategy="fcfs")

        view = cluster.wait_for(
            file_id,
            lambda view: any(point["state"] == "DISPATCHED"
                             for point in view["per_test"]),
            timeout=30, label="in-flight case for dead-node test")
        stuck = next((point for point in view["per_test"]
                      if point["state"] == "DISPATCHED"), None)
        require(stuck is not None, f"no dispatched point to reap: {view}")
        stuck_test = stuck["test_id"]
        dead_node = stuck["node_id"]
        live_node = "eval-02" if dead_node == "eval-01" else "eval-01"
        cluster.stop_process(f"{dead_node}-agent", force=True)
        cluster.stop_process(f"{dead_node}-worker", force=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            row = cluster.query(
                "SELECT target_node_id, dispatch_generation, state FROM submission_cases "
                "WHERE file_id=%s AND test_id=%s", (file_id, stuck_test))[0]
            if row[0] == live_node and row[2] in {"DONE", "CANCELLED"}:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"stranded case was not re-dispatched: {row}")
        view = wait_result(cluster, file_id, timeout=60)
        require(view["status"] == "FINAL", f"reaper run did not finalize: {view}")
        require(row[0] == live_node and row[1] >= 2,
                f"unexpected reaped case state: {row}")
        manifest["cases"].append({
            "case": "dead_node_reaper",
            "expected": f"re-dispatched to {live_node}",
            "stuck_test": stuck_test, "dead_node": dead_node,
            "case_row": row, "view": view,
        })

        # 5. Special fallback: a special task whose target eval node has no heartbeat
        #    must go through fallback-topic and still produce a final test result.
        config_row = cluster.query(
            "SELECT config, checker_hash FROM problems WHERE problem_id='special_pair'")[0]
        problem_config, checker_hash = config_row
        input_hash, answer_hash = problem_config["case_hashes"]["01"]
        output = b"0 5\n"
        out_hash = cluster.put_object(
            "outobj/" + hashlib.sha256(output).hexdigest() + ".out", output)
        file_id = str(uuid4())
        judge_id = f"{file_id}:01:1"
        policy = load_policy()
        limits = dict(policy["special"]["checker_limits"])
        limits.update({"input_hash": input_hash, "answer_hash": answer_hash,
                       "checker_hash": checker_hash, "out_bytes": 65536})
        cluster.execute(
            "INSERT INTO submissions(file_id,problem_id,source_hash,status,evaluation_mode,"
            "scheduling_strategy) VALUES (%s,'special_pair',%s,'EVALUATING','all_cases','fcfs')",
            (file_id, "sha256:" + "a" * 64))
        cluster.execute(
            "INSERT INTO submission_cases(file_id,test_id,sequence,attempt,judge_id,"
            "target_node_id,judge_mode,state,limits,dispatched_at) "
            "VALUES (%s,'01',1,1,%s,'ghost','special','DISPATCHED',%s,now())",
            (file_id, judge_id, Jsonb(limits)))
        now_ms = int(time.time() * 1000)
        task = {
            "ver": 1, "type": "special_task", "msg_id": f"{judge_id}:special:fallback",
            "file_id": file_id, "correlation_id": file_id, "problem_id": "special_pair",
            "test_id": "01", "sequence": 1, "attempt": 1, "judge_id": judge_id,
            "out_hash": out_hash, "ans_hash": answer_hash,
            "checker_hash": checker_hash, "input_hash": input_hash,
            "test_bundle_hash": problem_config.get("bundle_hash"),
            "limits": limits, "enqueue_seq": 1,
            "reply_to": {"eval_node_id": "ghost", "session_id": "ghost-session",
                         "reply_topic": "algoos.special.result.ghost",
                         "deadline_ms": now_ms + 30_000},
            "fallback_topic": "algoos.fallback", "emitted_at": "2026-11-16T00:00:00Z",
        }
        cluster.publish("algoos.special.task", task)
        view = wait_result(cluster, file_id, timeout=30)
        require(view["status"] == "FINAL" and view["verdict"] == "AC",
                f"special fallback failed: {view}")
        require(view["per_test"][0]["source"] == "fallback",
                f"special fallback source: {view['per_test']}")
        manifest["cases"].append({
            "case": "special_fallback_dead_target", "expected": "AC via fallback-topic",
            "view": view, "task_msg_id": task["msg_id"],
        })

        manifest["duration_s"] = round(time.monotonic() - started, 2)
        manifest.update(status="PASSED", exit_code=0)
        cluster.dump_logs(evidence / "logs")
        for path in sorted((ROOT / "examples").glob("*.cpp")):
            manifest.setdefault("example_sha256", {})[path.name] = hashlib.sha256(
                path.read_bytes()).hexdigest()
        print("[通过] 可靠性流程: 缓存、重复结果、Agent 重启、节点失联重派、特判 fallback")
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
    print(f"运行证据: {evidence.relative_to(ROOT)}")
    return int(manifest["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
