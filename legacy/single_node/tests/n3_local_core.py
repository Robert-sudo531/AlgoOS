"""N3.1：独立构建本地 C++ 核心，用已有 A+B 样例对照 AC/WA。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4
from zipfile import ZipFile

from judge.problem import load_problem, trim_common
from protocol.codec_v1 import dumps, loads


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify(root, work, evidence, run_id, manifest, cases):
    policy_path = root / "protocol/policy_v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    template = json.loads((root / "protocol/golden/eval_task.json").read_text(encoding="utf-8"))
    profile = policy["compiler_profiles"]["cpp_baseline_v1"]
    problem = load_problem(root / "problems/a_plus_b/problem.json")
    if [case.case_id for case in problem.tests] != ["01", "02"]:
        raise AssertionError("预期原 A+B 题包的 01、02 两个测试点")
    bundle = work / "test_bundle.zip"
    with ZipFile(bundle, "x") as archive:
        problem_dir = problem.config_path.parent
        for path in [problem.config_path, *(path for case in problem.tests for path in (case.input_path, case.output_path))]:
            archive.write(path, path.relative_to(problem_dir))
    bundle_hash = "sha256:" + sha256(bundle)
    manifest["test_bundle_hash"] = bundle_hash

    def build(command, timeout, log):
        record = {"command": command}
        manifest["builds"].append(record)
        print(json.dumps(command, ensure_ascii=False), file=log, flush=True)
        proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        record["exit_code"] = proc.returncode
        if proc.returncode:
            raise RuntimeError("构建失败，见 build.txt")

    native = work / "native"
    with (evidence / "build.txt").open("x", encoding="utf-8") as log:
        build(["make", "-C", str(root / "judge/native"), f"LOCAL_BUILD_DIR={native}", "eval-local"], 120, log)
        for label, source in (("ac", "a_plus_b.cpp"), ("wa", "wrong_answer.cpp")):
            build([profile["compiler"], *profile["flags"], str(root / "examples" / source),
                   "-o", str(work / label)], profile["limits"]["wall_ms"] / 1000, log)

    node_id = "local-eval-01"
    manifest["node_id"] = node_id
    for enqueue_seq, (label, verdict, reason) in enumerate(
        (("ac", "AC", "NONE"), ("wa", "WA", "ANSWER_MISMATCH")), 1
    ):
        binary = work / label
        file_id = f"{run_id}_{label}"
        for sequence, case in enumerate(problem.tests, 1):
            judge_id = f"{file_id}:{case.case_id}:1"
            task = dict(
                template, msg_id=f"task:{judge_id}", file_id=file_id, correlation_id=file_id,
                problem_id=problem.problem_id, test_id=case.case_id, sequence=sequence,
                attempt=1, judge_id=judge_id, enqueue_seq=enqueue_seq, target_node_id=node_id,
                emitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                bin_hash="sha256:" + sha256(binary), test_bundle_hash=bundle_hash,
                limits={"cpu_ms": problem.time_limit_ms, "wall_ms": problem.time_limit_ms,
                        "mem_bytes": problem.memory_limit_mib << 20, "out_bytes": case.out_bytes},
            )
            output_dir = work / f"{label}_{case.case_id}"
            command = [str(native / "eval_local"), str(policy_path), node_id, str(binary),
                       str(case.input_path), str(case.output_path), str(output_dir)]
            proc = subprocess.run(command, input=dumps(task, policy).encode("utf-8"),
                                  capture_output=True, timeout=10)
            record = {"command": command, "task": task, "cli_exit_code": proc.returncode,
                      "cli_stderr": proc.stderr.decode("utf-8", errors="replace")}
            cases.append(record)
            if proc.returncode:
                raise RuntimeError(f"{label}/{case.case_id}: {record['cli_stderr'].strip()}")
            message = loads(proc.stdout, policy)
            actual = (output_dir / "stdout.out").read_bytes()
            expected = case.output_path.read_bytes()
            record.update(result=message, actual=repr(actual), expected=repr(expected))
            expected_actual = expected if label == "ac" else {"01": b"1\n", "02": b"11\n"}[case.case_id]
            if actual != expected_actual or (trim_common(actual) == trim_common(expected)) != (verdict == "AC"):
                raise AssertionError(f"{label}/{case.case_id}: 输出与 N1 样例期望不符")
            if (message["verdict"], message["reason"], message["exit_code"], message["signal"]) != (verdict, reason, 0, 0):
                raise AssertionError(f"{label}/{case.case_id}: 判定或执行状态不符")
            for key in ("file_id", "correlation_id", "problem_id", "test_id", "sequence", "attempt", "judge_id"):
                if message[key] != task[key]:
                    raise AssertionError(f"{label}/{case.case_id}: {key} 不一致")
            if (message["node_id"] != node_id or message["out_bytes"] != len(trim_common(actual))
                    or message["memory_source"] != "rss_peak" or message["mem_bytes"] is None
                    or message["wall_ms"] is None or message["cpu_ms"] is None):
                raise AssertionError(f"{label}/{case.case_id}: 节点或测量字段不符")
            record["status"] = "PASSED"
            print(f"测试点 {case.case_id}：{verdict}/{reason}，实际={actual!r}，答案={expected!r}")

    # 使用上面已通过的一条任务，检查路由和输出限制错误会在执行前拒绝。
    for name, changes, diagnostic in [
        ("wrong_node", {"target_node_id": "another-node"}, "target node mismatch"),
        ("wrong_output_limit", {"limits": dict(task["limits"], out_bytes=case.out_bytes + 1)},
         "out_bytes differs from normalized answer length"),
    ]:
        guarded_task = dict(task, **changes)
        guard_dir = work / name
        proc = subprocess.run([*command[:-1], str(guard_dir)], input=dumps(guarded_task, policy).encode(),
                              capture_output=True, timeout=10)
        stderr = proc.stderr.decode("utf-8", errors="replace")
        manifest["guards"].append({"name": name, "exit_code": proc.returncode, "stderr": stderr})
        if proc.returncode != 1 or proc.stdout or guard_dir.exists() or diagnostic not in stderr:
            raise AssertionError(f"未在执行前正确拒绝：{name}")
    print("v1 身份与结果检查通过；2 项输入检查通过")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n3_local_core_{uuid4().hex[:12]}"
    work, evidence = root / "work" / run_id, root / "artifacts/system" / run_id
    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/native/eval_task_v1.hpp", "judge/native/eval_task_v1.cpp",
        "judge/native/Makefile", "judge/native/eval_core.hpp", "judge/native/eval_core.cpp",
        "judge/native/eval_local.cpp", "judge/problem.py", "protocol/codec_v1.hpp", "protocol/codec_v1.py",
        "protocol/policy_v1.json", "protocol/golden/eval_task.json", "tests/n3_local_core.py",
        "examples/a_plus_b.cpp", "examples/wrong_answer.cpp", "problems/a_plus_b/problem.json",
        *(f"problems/a_plus_b/tests/{case}.{ext}" for case in ("01", "02") for ext in ("in", "out")),
    ]
    manifest = {
        "run_id": run_id, "executed_by": args.actor, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "本地 C++ 正常退出的 AC/WA；题包和二进制哈希来自本次文件，入口只检查格式，尚不下载或验哈希",
        "source_sha256": {name: sha256(root / name) for name in inputs}, "builds": [], "guards": [],
    }
    cases = []
    try:
        verify(root, work, evidence, run_id, manifest, cases)
        manifest.update(status="PASSED", exit_code=0, verified_cases=len(cases))
        print("N3.1 C++ 本地核心验证通过：AC 2/2，WA 0/2")
    except Exception as error:
        manifest.update(status="FAILED", exit_code=1, error=str(error))
        print(f"N3.1 C++ 本地核心验证失败：{error}")
    write_json(evidence / "cases.json", cases)
    write_json(evidence / "manifest.json", manifest)
    print("运行证据：", evidence.relative_to(root))
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
