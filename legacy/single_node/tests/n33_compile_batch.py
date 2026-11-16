"""Real local N3.3 batch: submit, compile via JetStream, persist, dispatch."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import nats
from nats.js.api import AckPolicy, ConsumerConfig, StorageType, StreamConfig
import psycopg

from judge.problem import load_problem
from protocol.codec_v1 import loads
from scripts.compile_flow import enqueue_compile, publish_pending, save_compile_result
from scripts.compile_worker import handle_compile_message
from scripts.n32_local_services import local_services, read_config
from scripts.submissions import lookup, submit
from tests.n32_services import fixture_objects, require, write_json
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "deploy/n32"


async def verify(pg, storage, url, token, bucket, compiler, work, manifest, *,
                 eval_handler=None, submissions_ready=False, eval_nodes=("eval-01",), recovery=False,
                 problem_id="a_plus_b"):
    policy = json.loads((ROOT / "protocol/policy_v1.json").read_text())
    transport = policy["transport"]
    compile_subject = transport["compile_task_subject"]
    result_subject = transport["compile_result_subject"]
    eval_subjects = {node: transport["eval_task_subject_template"].format(target_node_id=node)
                     for node in eval_nodes}
    problem = load_problem(ROOT / "problems" / problem_id / "problem.json")
    if problem_id == "a_plus_b":
        _, bundle = fixture_objects()
    else:
        buffer = BytesIO()
        base = problem.config_path.parent
        with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
            for path in [problem.config_path, *(p for c in problem.tests for p in (c.input_path, c.output_path))]:
                archive.write(path, path.relative_to(base).as_posix())
        bundle = buffer.getvalue()
    bundle_digest = hashlib.sha256(bundle).hexdigest()
    storage.create_bucket(Bucket=bucket)
    storage.put_object(Bucket=bucket, Key=f"tests/{bundle_digest}.zip", Body=bundle)
    with psycopg.connect(**pg) as db:
        for name in (("compile_flow.sql",) if submissions_ready else ("submissions.sql", "compile_flow.sql")):
            db.execute((CONFIG / name).read_text())

    client = await nats.connect(url, token=token, allow_reconnect=False, connect_timeout=2)
    try:
        js = client.jetstream(timeout=3)
        await js.add_stream(config=StreamConfig(
            name="N33", subjects=[compile_subject, result_subject, *eval_subjects.values(),
                                  transport["eval_result_subject"]],
            storage=StorageType.FILE, max_msgs=32, max_bytes=1 << 20))
        async def subscription(subject, name):
            return await js.pull_subscribe(subject, durable=name, stream="N33",
                                           config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, ack_wait=60))

        compile_sub = await subscription(compile_subject, "compiler")
        result_sub = await subscription(result_subject, "control")
        eval_subs = {}
        for node, subject in eval_subjects.items():
            eval_subs[node] = await js.pull_subscribe(
                subject, durable=f"probe_{node}", stream="N33",
                config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT,
                                      ack_wait=2 if recovery and node == "eval-02" else 60))
        fixtures = [("a_plus_b.cpp", "COMPILED"), ("compile_error.cpp", "CE"), ("a_plus_b.cpp", "SE")]
        if eval_handler is not None:
            fixtures.insert(1, ("wrong_answer.cpp", "COMPILED"))
        if problem_id == "lazy_gold":
            fixtures = [("lazy_gold.cpp", "COMPILED")]
        for name, expected in fixtures:
            source = (ROOT / "examples" / name).read_bytes()
            if expected == "SE":
                source += b"\n// Separate fixture for the corrupt-object check.\n"
            file_id = submit(pg, storage, bucket, problem.problem_id, source)
            task = enqueue_compile(pg, file_id, problem, "sha256:" + bundle_digest, eval_nodes[0],
                                   policy, eval_nodes=eval_nodes)
            require(lookup(pg, file_id)["status"] == "QUEUED", "compile task was not recorded")
            if expected == "SE":
                key = "source/" + task["source_hash"].removeprefix("sha256:") + ".cpp"
                storage.put_object(Bucket=bucket, Key=key, Body=b"integrity-check fixture")
            await publish_pending(pg, js, policy)
            message, = await compile_sub.fetch(1, timeout=5)
            require(loads(message.data, policy) == task, "compile delivery mismatch")
            await handle_compile_message(message, js, policy, storage, bucket, compiler, work)
            notice, = await result_sub.fetch(1, timeout=5)
            result = loads(notice.data, policy)
            require(notice.subject == result_subject and result["file_id"] == str(file_id),
                    "compile notice mismatch")
            require(result["status"] == ("COMPILED" if expected == "COMPILED" else "FINAL")
                    and result["verdict"] == (None if expected == "COMPILED" else expected),
                    f"unexpected compile outcome: {result}")
            if expected == "CE":
                require(result["reason"] == "COMPILE_ERROR" and result["stderr_tail"], "missing CE diagnostics")
            if expected == "SE":
                require(result["reason"] == "SYSTEM_ERROR" and "SHA-256" in result["detail"],
                        "object mismatch not reported")
            save_compile_result(pg, result, policy)
            save_compile_result(pg, result, policy)  # Same notice must not create more dispatch entries.
            require(lookup(pg, file_id)["status"] == expected, "persisted status mismatch")
            expected_count = len(problem.tests) if expected == "COMPILED" else 0
            with psycopg.connect(**pg) as db:
                stored, = db.execute("SELECT result FROM algoos_compile_jobs WHERE file_id=%s",
                                     (file_id,)).fetchone()
                count, = db.execute("SELECT count(*) FROM algoos_outbox WHERE file_id=%s AND subject=ANY(%s) "
                                    "AND published_at IS NULL", (file_id, list(eval_subjects.values()))).fetchone()
            require(stored == result and count == expected_count,
                    "result or pending dispatch was not committed exactly once")
            await notice.ack_sync(timeout=3)
            await publish_pending(pg, js, policy)
            pending = sum([(await js.consumer_info("N33", f"probe_{node}")).num_pending
                           for node in eval_nodes])
            eval_tasks = []
            deliveries = []
            if expected == "COMPILED":
                require(pending == len(problem.tests), "incorrect number of eval tasks")
                binary = storage.get_object(Bucket=bucket, Key="bin/" + result["bin_hash"].removeprefix("sha256:"))
                with binary["Body"] as body:
                    require("sha256:" + hashlib.sha256(body.read()).hexdigest() == result["bin_hash"],
                            "binary content hash mismatch")
                for index, case in enumerate(problem.tests):
                    node = eval_nodes[index % len(eval_nodes)]
                    if recovery and node == "eval-02":
                        lost = await nats.connect(url, token=token, allow_reconnect=False, connect_timeout=2)
                        try:
                            temporary = await lost.jetstream().pull_subscribe_bind(
                                stream="N33", durable=f"probe_{node}")
                            abandoned, = await temporary.fetch(1, timeout=5)
                            require(abandoned.metadata.num_delivered == 1, "expected initial delivery")
                        finally:
                            await lost.close()  # Receiver vanishes before processing/ACK.
                        delivery, = await eval_subs[node].fetch(1, timeout=8)
                        require(delivery.data == abandoned.data
                                and delivery.metadata.sequence.stream == abandoned.metadata.sequence.stream
                                and delivery.metadata.num_delivered == 2, "expected identical broker redelivery")
                        manifest.setdefault("redeliveries", []).append(dict(
                            stream_sequence=delivery.metadata.sequence.stream,
                            count=delivery.metadata.num_delivered, node=node,
                            judge_id=loads(delivery.data, policy)["judge_id"]))
                    else:
                        delivery, = await eval_subs[node].fetch(1, timeout=5)
                    event = loads(delivery.data, policy)
                    require(delivery.subject == eval_subjects[node] and event["target_node_id"] == node
                            and event["type"] == "eval_task"
                            and event["file_id"] == str(file_id) and event["test_id"] == case.case_id
                            and event["bin_hash"] == result["bin_hash"]
                            and event["test_bundle_hash"] == task["test_bundle_hash"]
                            and event["limits"]["out_bytes"] == case.out_bytes, "eval task mismatch")
                    eval_tasks.append(event)
                    if eval_handler is None:
                        await delivery.ack_sync(timeout=3)  # Probe reads only; no test execution here.
                    else:
                        deliveries.append(delivery)
                if eval_handler is not None:
                    await eval_handler(js, policy, deliveries, name)
            else:
                require(pending == 0 and "bin_hash" not in result,
                        "failed compile should not dispatch evaluation")
            manifest["cases"].append(dict(fixture=name, status=expected, task=task,
                                          result=result, eval_tasks=eval_tasks))
            print(f"[通过] {expected}：状态可查询，评测任务 {len(eval_tasks)} 条", flush=True)
        require(all(info.num_ack_pending == 0 and info.num_pending == 0 for info in
                    [await js.consumer_info("N33", name) for name in
                     ("compiler", "control", *(f"probe_{node}" for node in eval_nodes))]),
                "messages remain pending")
        with psycopg.connect(**pg) as db:
            require(db.execute("SELECT count(*) FROM algoos_outbox WHERE published_at IS NULL").fetchone()[0] == 0,
                    "unpublished outbox rows")
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    run_id = "n33_compile_batch_" + uuid4().hex[:12]
    work, evidence = ROOT / "work" / run_id, ROOT / "artifacts/system" / run_id
    work.mkdir(parents=True)
    evidence.mkdir(parents=True)
    files = ["deploy/n32/" + name for name in ("local.json", "submissions.sql", "compile_flow.sql")]
    files += ["scripts/" + name for name in ("submissions.py", "compile_flow.py", "compile_worker.py",
                                             "n32_local_services.py")]
    files += ["judge/native/Makefile", "judge/native/compile_local.cpp", "judge/problem.py",
              "scripts/eval_scheduler.py",
              "protocol/codec_v1.py", "protocol/policy_v1.json", "tests/n32_services.py",
              "tests/n33_compile_batch.py", "examples/a_plus_b.cpp", "examples/compile_error.cpp"]
    files += [str(path.relative_to(ROOT)) for path in sorted((ROOT / "problems/a_plus_b").rglob("*"))
              if path.is_file()]
    manifest = dict(executed_by=args.actor, cases=[], scope="local compile and dispatch; no evaluation",
                    source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files})
    try:
        with (evidence / "build.txt").open("wb") as log:
            subprocess.run(["make", "-C", str(ROOT / "judge/native"), f"COMPILE_BUILD_DIR={work}",
                            "compile-local"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        config = read_config(ROOT, CONFIG / "local.json")
        with local_services(config, evidence, manifest) as (pg, storage, url, token):
            asyncio.run(verify(pg, storage, url, token, "algoos-" + run_id.rsplit("_", 1)[-1],
                               work / "compile-local", work, manifest))
        manifest.update(status="PASSED", exit_code=0)
        print("N3.3 提交、编译与派发批次验证通过")
    except Exception as error:
        manifest.update(status="FAILED", exit_code=1, error=str(error))
        print(f"验证失败：{error}")
    write_json(evidence / "manifest.json", manifest)
    print("运行证据：", evidence.relative_to(ROOT))
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
