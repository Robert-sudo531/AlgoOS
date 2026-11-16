"""Local queue adapter and transactional all-cases aggregation."""
import asyncio
from contextlib import closing
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from uuid import UUID
from zipfile import ZipFile

import psycopg
from psycopg.types.json import Jsonb

from protocol.codec_v1 import dumps, loads, normalize
from scripts.compile_worker import MAX_BINARY_BYTES


def fetch(storage, bucket, prefix, digest, suffix, cap):
    key = prefix + "/" + digest.removeprefix("sha256:") + suffix
    response = storage.get_object(Bucket=bucket, Key=key)
    with closing(response["Body"]) as body:
        data = body.read(cap + 1)
    if len(data) > cap or "sha256:" + sha256(data).hexdigest() != digest:
        raise ValueError(f"{prefix} size or SHA-256 mismatch")
    return data


def run_eval(task, policy, storage, bucket, worker, config, work):
    binary = fetch(storage, bucket, "bin", task["bin_hash"], "", MAX_BINARY_BYTES)
    bundle = fetch(storage, bucket, "tests", task["test_bundle_hash"], ".zip", 24 << 20)
    with ZipFile(BytesIO(bundle)) as archive:
        def member(name):
            if archive.getinfo(name).file_size > 2 << 20:
                raise ValueError("bundle entry exceeds local size cap")
            return archive.read(name)

        problem = json.loads(member("problem.json"))
        if problem["problem_id"] != task["problem_id"]:
            raise ValueError("bundle problem mismatch")
        case = problem["tests"][task["sequence"] - 1]
        if str(case["case_id"]) != task["test_id"]:
            raise ValueError("bundle case mismatch")
        # Never extract archive paths into the filesystem.
        input_data, answer_data = member(case["input"]), member(case["output"])
    with TemporaryDirectory(prefix="eval_inputs_", dir=work) as directory:
        directory = Path(directory)
        program, input_file, answer = (directory / name for name in ("program", "input", "answer"))
        program.write_bytes(binary)
        program.chmod(0o700)
        input_file.write_bytes(input_data)
        answer.write_bytes(answer_data)
        run_dir = directory / "runs"
        run_dir.mkdir()
        settings = json.loads(config.read_text())
        settings["work_dir"] = str(run_dir)
        local_config = directory / "config.json"
        local_config.write_text(json.dumps(settings))
        proc = subprocess.run([worker, "run", local_config, program, input_file, answer, "case"],
                              input=dumps(task, policy).encode(), capture_output=True, check=True,
                              timeout=task["limits"]["wall_ms"] / 1000 + 10)
        return loads(proc.stdout, policy)


async def handle_eval_message(message, js, policy, storage, bucket, worker, config, work):
    task = loads(message.data, policy)
    subject = policy["transport"]["eval_task_subject_template"].format(
        target_node_id=task["target_node_id"])
    if task["type"] != "eval_task" or message.subject != subject:
        raise ValueError("unexpected eval task or subject")
    result = await asyncio.to_thread(run_eval, task, policy, storage, bucket, worker, config, work)
    await js.publish(policy["transport"]["eval_result_subject"], dumps(result, policy).encode(),
                     headers={"Nats-Msg-Id": result["msg_id"]}, timeout=3)
    await message.ack_sync(timeout=3)


def save_eval_result(pg, result, policy):
    result = normalize(result, policy)
    if result["type"] != "eval_result" or result["stage"] != "eval":
        raise ValueError("expected eval-stage result")
    file_id = UUID(result["file_id"])
    with psycopg.connect(**pg) as db:
        job = db.execute("SELECT eval_plan,result FROM algoos_compile_jobs WHERE file_id=%s FOR UPDATE",
                         (file_id,)).fetchone()
        if job is None or not job[1] or job[1]["status"] != "COMPILED":
            raise ValueError("no successful compile for result")
        plan, compiled = job
        if result["attempt"] != compiled["attempt"] or result["problem_id"] != compiled["problem_id"]:
            raise ValueError("result attempt or problem mismatch")
        case = next((item for item in plan if item["sequence"] == result["sequence"]), None)
        if not case or (case["test_id"], case["target_node_id"]) != (result["test_id"], result["node_id"]):
            raise ValueError("result case or node mismatch")
        prior = db.execute("SELECT result FROM algoos_eval_results WHERE judge_id=%s",
                           (result["judge_id"],)).fetchone()
        if prior:
            if prior[0] != result:
                raise ValueError("conflicting eval result")
            return
        db.execute("INSERT INTO algoos_eval_results(judge_id,file_id,sequence,result) VALUES (%s,%s,%s,%s)",
                   (result["judge_id"], file_id, result["sequence"], Jsonb(result)))
        verdicts = db.execute("SELECT result->>'verdict' FROM algoos_eval_results "
                              "WHERE file_id=%s ORDER BY sequence", (file_id,)).fetchall()
        if len(verdicts) == len(plan):
            final = next((verdict for verdict, in verdicts if verdict != "AC"), "AC")
            db.execute("UPDATE algoos_submissions SET status=%s WHERE file_id=%s", (final, file_id))
