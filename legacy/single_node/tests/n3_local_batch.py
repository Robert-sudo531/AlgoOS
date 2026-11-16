"""N3.1 本地批次：一次构建核心，回归已有判定并验证输出超限。"""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import signal
import subprocess
from uuid import uuid4

from judge.problem import trim_common
from protocol.codec_v1 import dumps, loads
from tests.n3_crash_signal import verify_crash_signal
from tests.n3_exit_paths import verify_exit_paths
from tests.n3_local_core import sha256, verify as verify_ac_wa, write_json
from tests.n3_resources import ResourceUnavailable, verify_cpu, verify_cpu_hard, verify_memory, verify_resource_decisions
from tests.n3_wall_timeout import verify_wall_timeout
from tests.n3_worker_delivery import verify_worker_delivery


def verify_output_limit(root, work, evidence, run_id, manifest, cases):
    policy = json.loads((root / "protocol/policy_v1.json").read_text(encoding="utf-8"))
    profile = policy["compiler_profiles"]["cpp_baseline_v1"]
    binary = work / "output_limit"
    command = [profile["compiler"], *profile["flags"], str(root / "examples/output_limit.cpp"), "-o", str(binary)]
    with (evidence / "build.txt").open("a", encoding="utf-8") as log:
        print(json.dumps(command, ensure_ascii=False), file=log, flush=True)
        proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                              timeout=profile["limits"]["wall_ms"] / 1000)
    manifest["builds"].append({"command": command, "exit_code": proc.returncode})
    if proc.returncode:
        raise RuntimeError("输出超限样例编译失败，见 build.txt")

    file_id = f"{run_id}_output"
    bin_hash = "sha256:" + sha256(binary)
    for baseline in cases[:2]:
        case_id = baseline["task"]["test_id"]
        judge_id = f"{file_id}:{case_id}:1"
        task = dict(baseline["task"], file_id=file_id, correlation_id=file_id, judge_id=judge_id,
                    msg_id=f"task:{judge_id}", enqueue_seq=8, bin_hash=bin_hash,
                    emitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
        output_dir = work / f"output_{case_id}"
        base_command = baseline["command"]
        command = [*base_command[:3], str(binary), *base_command[4:6], str(output_dir)]
        proc = subprocess.run(command, input=dumps(task, policy).encode("utf-8"),
                              capture_output=True, timeout=10)
        record = {"name": "output_limit", "command": command, "task": task, "cli_exit_code": proc.returncode,
                  "cli_stderr": proc.stderr.decode("utf-8", errors="replace")}
        cases.append(record)
        if proc.returncode:
            raise AssertionError(f"{case_id} 未生成输出超限结果：{record['cli_stderr'].strip()}")
        result = loads(proc.stdout, policy)
        actual = (output_dir / "stdout.out").read_bytes()
        limit = task["limits"]["out_bytes"]
        file_cap = min(policy["runtime"]["fsize_max_bytes"], limit + policy["runtime"]["fsize_slack_bytes"])
        record.update(result=result, captured_bytes=len(actual), actual_prefix=repr(actual[:64]), file_cap=file_cap)
        if (result["verdict"], result["reason"], result["wall_timeout"]) != ("WA", "OUTPUT_LIMIT", False):
            raise AssertionError(f"{case_id} 输出超限判定不符：{result['verdict']}/{result['reason']}")
        for key in ("file_id", "correlation_id", "problem_id", "test_id", "sequence", "attempt", "judge_id"):
            if result[key] != task[key]:
                raise AssertionError(f"{case_id} 输出超限结果的 {key} 不一致")
        if result["node_id"] != task["target_node_id"]:
            raise AssertionError(f"{case_id} 执行节点不一致")
        if case_id == "01":
            length = len(trim_common(actual))
            if actual != b"3 extra\n" or not limit < length <= file_cap or (
                result["exit_code"], result["signal"], result["out_bytes"],
            ) != (0, 0, length):
                raise AssertionError("正常退出的超长输出应保留归一化长度")
            print(f"测试点 01：WA/OUTPUT_LIMIT，退出码 0，归一化输出 {length} 字节，上限 {limit} 字节")
        else:
            if actual != b"x" * file_cap or (
                result["exit_code"], result["signal"], result["out_bytes"],
            ) != (None, int(signal.SIGXFSZ), None):
                raise AssertionError("文件触顶应由实际 SIGXFSZ 确认，完整输出长度为空")
            print(f"测试点 02：WA/OUTPUT_LIMIT，信号 {result['signal']}（SIGXFSZ），已写 {len(actual)} 字节")
        record["status"] = "PASSED"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明")
    parser.add_argument("--resources", action="store_true", help="同时验收 CPU、地址空间、错误摘要和合成内存判定")
    parser.add_argument("--delivery", action="store_true", help="同时验证独立 Worker 归档和源码外运行")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n3_local_batch_{uuid4().hex[:12]}"
    work, evidence = root / "work" / run_id, root / "artifacts/system" / run_id
    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/native/Makefile", "judge/native/eval_core.hpp", "judge/native/eval_core.cpp",
        "judge/native/eval_local.cpp", "judge/problem.py", "protocol/codec_v1.hpp", "protocol/codec_v1.py",
        "protocol/policy_v1.json", "protocol/golden/eval_task.json", "tests/n3_local_core.py",
        "tests/n3_exit_paths.py", "tests/n3_wall_timeout.py", "tests/n3_crash_signal.py", "tests/n3_local_batch.py",
        "examples/a_plus_b.cpp", "examples/wrong_answer.cpp", "examples/runtime_error.cpp", "examples/return_125.cpp",
        "examples/time_limit.cpp", "examples/crash_signal.cpp", "examples/output_limit.cpp",
        "problems/a_plus_b/problem.json",
        *(f"problems/a_plus_b/tests/{case}.{ext}" for case in ("01", "02") for ext in ("in", "out")),
    ]
    inputs.append("tests/n3_resources.py")
    inputs.extend(["judge/native/eval_task_v1.hpp", "judge/native/eval_task_v1.cpp", "tests/n3_worker_delivery.py"])
    if args.resources:
        inputs.extend(["examples/cpu_limit.cpp", "examples/memory_limit.cpp", "tests/n3_resource_decisions.cpp"])
    if args.delivery:
        inputs.extend(["judge/native/eval_worker.cpp", "judge/native/eval-worker.example.json", "scripts/package_eval_worker.py"])
    manifest = {
        "run_id": run_id, "executed_by": args.actor, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "本地真实执行：AC/WA、RE/SE、墙钟 TLE、SIGABRT、归一化输出和文件上限；不验证 CPU/MLE 或 Worker 部署",
        "source_sha256": {name: sha256(root / name) for name in inputs}, "builds": [], "guards": [], "checks": [],
    }
    cases = []
    groups = [
        ("AC/WA 与输入检查", verify_ac_wa), ("非零退出与启动失败", verify_exit_paths),
        ("墙钟超时", verify_wall_timeout), ("崩溃信号", verify_crash_signal), ("输出超限", verify_output_limit),
    ]
    manifest["synthetic_checks"] = 0
    if args.resources:
        manifest["scope"] = "本地判定回归、真实 CPU/地址空间与诊断、合成内存规则；实际 OOM 与 Worker 部署待验收"
        groups.extend([("CPU 软限制与实测超限", verify_cpu), ("CPU 硬限制", verify_cpu_hard),
                       ("地址空间、RSS 与错误摘要", verify_memory), ("内存判定规则（合成证据）", verify_resource_decisions)])
    if args.delivery:
        manifest["scope"] += "；另验收本地 Worker 归档和源码外一次性调用"
        groups.append(("Worker 打包与源码外调用", verify_worker_delivery))
    print("N3.1 本地判定批次汇总：", flush=True)
    baseline_ok = False
    for index, (label, verify) in enumerate(groups):
        record = {"name": label}
        manifest["checks"].append(record)
        if index and not baseline_ok:
            record.update(status="SKIPPED", error="基础构建或 AC/WA 检查未通过")
            print(f"[跳过] {label}：{record['error']}", flush=True)
            continue
        start = len(cases)
        synthetic_start = manifest["synthetic_checks"]
        log = StringIO()
        with redirect_stdout(log):
            try:
                verify(root, work, evidence, run_id, manifest, cases)
                record["status"] = "PASSED"
            except ResourceUnavailable as error:
                record.update(status="SKIPPED", error=str(error))
            except Exception as error:
                record.update(status="FAILED", error=str(error))
        record["output"] = log.getvalue()
        record["passed_cases"] = sum(item.get("status") == "PASSED" for item in cases[start:])
        record["synthetic_checks"] = manifest["synthetic_checks"] - synthetic_start
        if index == 0:
            baseline_ok = record["status"] == "PASSED"
        if record["status"] == "PASSED":
            count = (f"{record['synthetic_checks']} 项合成检查" if record["synthetic_checks"]
                     else f"{record['passed_cases']} 个用例")
            print(f"[通过] {label}（{count}）", flush=True)
        elif record["status"] == "SKIPPED":
            print(f"[跳过] {label}：{record['error']}", flush=True)
        else:
            print(f"[失败] {label}：{record['error']}", flush=True)

    passed = all(item["status"] == "PASSED" for item in manifest["checks"])
    manifest.update(status="PASSED" if passed else "FAILED", exit_code=0 if passed else 1,
                    verified_cases=sum(item.get("status") == "PASSED" for item in cases))
    write_json(evidence / "cases.json", cases)
    write_json(evidence / "manifest.json", manifest)
    if passed:
        extra = f"，{manifest['synthetic_checks']} 项合成检查" if args.resources else ""
        print(f"N3.1 本地判定批次验证通过：{manifest['verified_cases']} 个用例，{len(manifest['guards'])} 项输入检查{extra}")
    else:
        print("N3.1 本地判定批次未全部通过，见上方失败或跳过项")
    print("运行证据：", evidence.relative_to(root))
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
