"""N3.4 local acceptance: compile, run, collect and query one submission."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import UUID, uuid4

from nats.js.api import AckPolicy, ConsumerConfig
import psycopg

from protocol.codec_v1 import loads
from scripts.eval_flow import handle_eval_message, save_eval_result
from scripts.n32_local_services import local_services, read_config
from scripts.submissions import lookup
from tests.n32_services import require, write_json
from tests.n33_compile_batch import CONFIG, ROOT, verify as verify_compile


async def verify(pg, storage, url, token, bucket, work, manifest, nodes=("eval-01",), recovery=False,
                 problem_id="a_plus_b"):
    with psycopg.connect(**pg) as db:
        db.execute((CONFIG / "submissions.sql").read_text())
        db.execute((CONFIG / "eval_flow.sql").read_text())
    configs = {}
    for node in nodes:
        directory = work / node
        directory.mkdir()
        config = directory / "eval.json"
        write_json(config, dict(ver=1, node_id=node,
                                policy_file=str(ROOT / "protocol/policy_v1.json"), work_dir=str(directory)))
        configs[node] = config
    subscriber = None

    async def evaluate(js, policy, deliveries, fixture):
        nonlocal subscriber
        subject = policy["transport"]["eval_result_subject"]
        if subscriber is None:
            subscriber = await js.pull_subscribe(
                subject, stream="N33", durable="aggregator",
                config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, ack_wait=60))
        file_id = UUID(loads(deliveries[0].data, policy)["file_id"])
        expected = "WA" if fixture == "wrong_answer.cpp" else "AC"
        outcomes = []
        if len(nodes) > 1:
            require([loads(item.data, policy)["target_node_id"] for item in deliveries] ==
                    [nodes[i % len(nodes)] for i in range(len(deliveries))],
                    "expected both nodes in case sequence")
            sequences = [item.metadata.sequence.stream for item in deliveries]
            require(sequences == sorted(set(sequences)), "case dispatch order differs from sequence")
            async def run_on_node(delivery):
                node = loads(delivery.data, policy)["target_node_id"]
                await handle_eval_message(delivery, js, policy, storage, bucket,
                                          work / "eval-worker", configs[node], configs[node].parent)
            async def run_assigned(node):
                for item in deliveries:
                    if loads(item.data, policy)["target_node_id"] == node:
                        await run_on_node(item)
            await asyncio.gather(*(run_assigned(node) for node in nodes))
        for delivery in reversed(deliveries):
            if len(nodes) == 1:
                await handle_eval_message(delivery, js, policy, storage, bucket, work / "eval-worker",
                                          configs[nodes[0]], configs[nodes[0]].parent)
            event, = await subscriber.fetch(1, timeout=5)
            result = loads(event.data, policy)
            require(event.subject == subject and result["file_id"] == str(file_id), "wrong result identity")
            require((result["verdict"], result["reason"], result["exit_code"]) ==
                    (expected, "NONE" if expected == "AC" else "ANSWER_MISMATCH", 0),
                    "unexpected real verdict")
            save_eval_result(pg, result, policy)
            save_eval_result(pg, result, policy)
            outcomes.append(result)
            expected_status = expected if len(outcomes) == len(deliveries) else "COMPILED"
            require(lookup(pg, file_id)["status"] == expected_status, "incorrect submission status")
            await event.ack_sync(timeout=3)
        if recovery:
            # Use a distinct broker dedup header so the identical payload actually arrives.
            ack = await js.publish(subject, event.data, headers={"Nats-Msg-Id": "replay:" + result["msg_id"]})
            require(not ack.duplicate, "broker suppressed the duplicate probe")
            repeated, = await subscriber.fetch(1, timeout=5)
            require(repeated.data == event.data, "expected repeated result payload")
            save_eval_result(pg, loads(repeated.data, policy), policy)
            require(lookup(pg, file_id)["status"] == expected, "replay changed final verdict")
            await repeated.ack_sync(timeout=3)
            manifest.setdefault("duplicate_results", []).append(dict(
                file_id=str(file_id), judge_id=result["judge_id"], stream_sequence=ack.seq))
        if len(nodes) == 1:
            require([r["sequence"] for r in outcomes] == [2, 1], "expected out-of-order results")
        else:
            require({r["node_id"] for r in outcomes} == set(nodes), "both nodes must return results")
        with psycopg.connect(**pg) as db:
            rows = db.execute("SELECT result FROM algoos_eval_results WHERE file_id=%s ORDER BY sequence",
                              (file_id,)).fetchall()
        require(rows == [(result,) for result in sorted(outcomes, key=lambda item: item["sequence"])],
                "persisted test points differ")
        info = await js.consumer_info("N33", "aggregator")
        require(info.num_pending == info.num_ack_pending == 0, "result queue is not drained")
        manifest["evaluations"].append(dict(fixture=fixture, file_id=str(file_id), status=expected, results=outcomes))
        arrived = "、".join(f"{item['test_id']}@{item['node_id']}" for item in outcomes)
        print(f"[通过] {expected}：{arrived} 回传，去重、汇总与查询通过", flush=True)

    await verify_compile(pg, storage, url, token, bucket, work / "compile-local", work, manifest,
                         eval_handler=evaluate, submissions_ready=True, eval_nodes=nodes, recovery=recovery,
                         problem_id=problem_id)
    with psycopg.connect(**pg) as db:
        require(db.execute("SELECT count(*) FROM algoos_eval_results").fetchone()[0] ==
                sum(len(item["results"]) for item in manifest["evaluations"]),
                "unexpected evaluation rows")
    if recovery:
        require(len(manifest["redeliveries"]) == len(manifest["duplicate_results"]) == 2,
                "missing recovery evidence")
        print("[通过] 两次节点连接中断后原任务重投；两条重复结果未增加落库行数", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--two-nodes", action="store_true", help="also verify two local node routes")
    parser.add_argument("--recovery", action="store_true", help="verify redelivery and duplicate results on two nodes")
    parser.add_argument("--lazy-gold", action="store_true", help="run the restored contest fixture on two local nodes")
    args = parser.parse_args()
    run = uuid4().hex[:12]
    if args.lazy_gold and args.recovery:
        parser.error("--lazy-gold and --recovery are separate runs")
    problem_id = "lazy_gold" if args.lazy_gold else "a_plus_b"
    batch = ("n4_lazy_gold" if args.lazy_gold else "n36_recovery" if args.recovery else
             "n35_two_nodes" if args.two_nodes else "n34_eval_batch")
    nodes = ("eval-01", "eval-02") if args.two_nodes or args.recovery or args.lazy_gold else ("eval-01",)
    work, evidence = (ROOT / base / f"{batch}_{run}" for base in ("work", "artifacts/system"))
    work.mkdir(parents=True)
    evidence.mkdir(parents=True)
    paths = [ROOT / name for name in (
        "tests/n34_eval_batch.py", "tests/n33_compile_batch.py", "tests/n32_services.py",
        "scripts/eval_flow.py", "scripts/compile_flow.py", "scripts/compile_worker.py",
        "scripts/submissions.py", "scripts/n32_local_services.py", "scripts/eval_scheduler.py", "judge/native/Makefile",
        "judge/problem.py", "protocol/policy_v1.json", "protocol/codec_v1.py", "protocol/codec_v1.hpp")]
    paths += sorted((ROOT / "judge/native").glob("*.cpp")) + sorted((ROOT / "judge/native").glob("*.hpp"))
    paths += sorted(CONFIG.glob("*.sql")) + [CONFIG / "local.json"]
    paths += sorted((ROOT / "examples").glob("*.cpp"))
    paths += sorted(path for path in (ROOT / "problems" / problem_id).rglob("*") if path.is_file())
    manifest = dict(executed_by=args.actor, cases=[], evaluations=[], scope="trusted local fixtures",
                    nodes=nodes, policy="FCFS publish order with round-robin case assignment",
                    source_sha256={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in paths})
    try:
        with (evidence / "build.txt").open("wb") as log:
            subprocess.run(["make", "-C", str(ROOT / "judge/native"), f"COMPILE_BUILD_DIR={work}",
                            f"WORKER_BUILD_DIR={work}", "compile-local", "eval-worker"],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        config = read_config(ROOT, CONFIG / "local.json")
        with local_services(config, evidence, manifest) as (pg, storage, url, token):
            asyncio.run(verify(pg, storage, url, token, "algoos-" + run, work, manifest, nodes, args.recovery,
                               problem_id))
        manifest.update(status="PASSED", exit_code=0)
        print("Lazy Gold 竞赛题双节点回放通过" if args.lazy_gold else
              "N3.6 本机重投与结果去重验证通过" if args.recovery else
              "N3.5 本机两节点分发验证通过" if args.two_nodes else "N3.4 单节点评测、结果汇总与查询验证通过")
    except Exception as error:
        manifest.update(status="FAILED", exit_code=1, error=str(error))
        print(f"验证失败：{error}")
    write_json(evidence / "manifest.json", manifest)
    print("运行证据：", evidence.relative_to(ROOT))
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
