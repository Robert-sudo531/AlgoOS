"""N3.1：实测 SIGABRT 崩溃判定，并回归已有判定。"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import subprocess
from uuid import uuid4

from protocol.codec_v1 import dumps, loads
from tests.n3_exit_paths import verify_exit_paths
from tests.n3_local_core import sha256, verify as verify_ac_wa, write_json
from tests.n3_wall_timeout import verify_wall_timeout


def verify_crash_signal(root, work, evidence, run_id, manifest, cases):
    baseline = cases[0]  # 复用 A+B 测试点 01 和本地执行配置。
    base_command = baseline["command"]
    policy = json.loads(Path(base_command[1]).read_text(encoding="utf-8"))
    profile = policy["compiler_profiles"]["cpp_baseline_v1"]
    binary = work / "crash_signal"
    command = [profile["compiler"], *profile["flags"], str(root / "examples/crash_signal.cpp"), "-o", str(binary)]
    with (evidence / "build.txt").open("a", encoding="utf-8") as log:
        print(json.dumps(command, ensure_ascii=False), file=log, flush=True)
        proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                              timeout=profile["limits"]["wall_ms"] / 1000)
    manifest["builds"].append({"command": command, "exit_code": proc.returncode})
    if proc.returncode:
        raise RuntimeError("崩溃样例编译失败，见 build.txt")

    file_id = f"{run_id}_abort"
    judge_id = f"{file_id}:01:1"
    task = dict(baseline["task"], file_id=file_id, correlation_id=file_id, judge_id=judge_id,
                msg_id=f"task:{judge_id}", enqueue_seq=7, bin_hash="sha256:" + sha256(binary),
                emitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
    output_dir = work / "abort_01"
    command = [*base_command[:3], str(binary), *base_command[4:6], str(output_dir)]
    proc = subprocess.run(command, input=dumps(task, policy).encode("utf-8"),
                          capture_output=True, timeout=10)
    record = {"name": "crash_signal", "command": command, "task": task, "cli_exit_code": proc.returncode,
              "cli_stderr": proc.stderr.decode("utf-8", errors="replace")}
    cases.append(record)
    if proc.returncode:
        raise AssertionError(f"未生成崩溃结果：{record['cli_stderr'].strip()}")
    result = loads(proc.stdout, policy)
    actual = (output_dir / "stdout.out").read_bytes()
    stderr = (output_dir / "stderr.err").read_bytes()
    record.update(result=result, actual=repr(actual), program_stderr=repr(stderr))
    if (result["verdict"], result["reason"], result["signal"], result["exit_code"], result["wall_timeout"]) != (
        "RE", "CRASH_SIGNAL", int(signal.SIGABRT), None, False,
    ):
        raise AssertionError("SIGABRT 应判 RE/CRASH_SIGNAL，正常退出码为空，未触发墙钟超时")
    if actual != Path(base_command[5]).read_bytes() or result["out_bytes"] is not None:
        raise AssertionError("崩溃前应保留正确输出，未比较的输出长度应为空")
    if (any(result[key] is None for key in ("wall_ms", "cpu_ms", "mem_bytes"))
            or result["memory_source"] != "rss_peak"):
        raise AssertionError("崩溃结果缺少实际测量值")
    for key in ("file_id", "correlation_id", "problem_id", "test_id", "sequence", "attempt", "judge_id"):
        if result[key] != task[key]:
            raise AssertionError(f"崩溃结果的 {key} 不一致")
    if result["node_id"] != task["target_node_id"]:
        raise AssertionError("崩溃结果的执行节点不一致")
    record["status"] = "PASSED"
    print(f"测试点 01：RE/CRASH_SIGNAL，信号={result['signal']}（SIGABRT），"
          f"程序退出码=null，未触发墙钟超时，崩溃前输出={actual!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n3_crash_signal_{uuid4().hex[:12]}"
    work, evidence = root / "work" / run_id, root / "artifacts/system" / run_id
    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/native/eval_task_v1.hpp", "judge/native/eval_task_v1.cpp",
        "judge/native/Makefile", "judge/native/eval_core.hpp", "judge/native/eval_core.cpp",
        "judge/native/eval_local.cpp", "judge/problem.py", "protocol/codec_v1.hpp", "protocol/codec_v1.py",
        "protocol/policy_v1.json", "protocol/golden/eval_task.json", "tests/n3_local_core.py",
        "tests/n3_exit_paths.py", "tests/n3_wall_timeout.py", "tests/n3_crash_signal.py",
        "examples/a_plus_b.cpp", "examples/wrong_answer.cpp", "examples/runtime_error.cpp",
        "examples/return_125.cpp", "examples/time_limit.cpp", "examples/crash_signal.cpp",
        "problems/a_plus_b/problem.json",
        *(f"problems/a_plus_b/tests/{case}.{ext}" for case in ("01", "02") for ext in ("in", "out")),
    ]
    manifest = {
        "run_id": run_id, "executed_by": args.actor, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "真实 SIGABRT 崩溃、墙钟 TLE 和 AC/WA/RE/SE 回归；其他崩溃信号及资源超限未单独实测",
        "source_sha256": {name: sha256(root / name) for name in inputs}, "builds": [], "guards": [],
    }
    cases = []
    try:
        verify_ac_wa(root, work, evidence, run_id, manifest, cases)
        verify_exit_paths(root, work, evidence, run_id, manifest, cases)
        verify_wall_timeout(root, work, evidence, run_id, manifest, cases)
        verify_crash_signal(root, work, evidence, run_id, manifest, cases)
        manifest.update(status="PASSED", exit_code=0, verified_cases=len(cases))
        print("N3.1 崩溃信号验证通过：SIGABRT 判 RE，墙钟 TLE 和已有判定回归通过")
    except Exception as error:
        manifest.update(status="FAILED", exit_code=1, error=str(error))
        print(f"N3.1 崩溃信号验证失败：{error}")
    write_json(evidence / "cases.json", cases)
    write_json(evidence / "manifest.json", manifest)
    print("运行证据：", evidence.relative_to(root))
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
