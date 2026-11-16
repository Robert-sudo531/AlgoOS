"""N3.1：实测墙钟超时、终止信号和超时前输出，回归已有判定。"""

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


def verify_wall_timeout(root, work, evidence, run_id, manifest, cases):
    policy = json.loads((root / "protocol/policy_v1.json").read_text(encoding="utf-8"))
    profile = policy["compiler_profiles"]["cpp_baseline_v1"]
    binary = work / "wall_timeout"
    command = [profile["compiler"], *profile["flags"], str(root / "examples/time_limit.cpp"), "-o", str(binary)]
    with (evidence / "build.txt").open("a", encoding="utf-8") as log:
        print(json.dumps(command, ensure_ascii=False), file=log, flush=True)
        proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                              timeout=profile["limits"]["wall_ms"] / 1000)
    manifest["builds"].append({"command": command, "exit_code": proc.returncode})
    if proc.returncode:
        raise RuntimeError("休眠样例编译失败，见 build.txt")

    file_id = f"{run_id}_wall"
    bin_hash = "sha256:" + sha256(binary)
    for baseline in cases[:2]:  # 复用正确 A+B 的两个测试点和 1000 ms 时限。
        original_task = baseline["task"]
        case_id = original_task["test_id"]
        judge_id = f"{file_id}:{case_id}:1"
        task = dict(original_task, file_id=file_id, correlation_id=file_id, judge_id=judge_id,
                    msg_id=f"task:{judge_id}", enqueue_seq=6, bin_hash=bin_hash,
                    emitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
        if task["limits"]["wall_ms"] != 1000:
            raise AssertionError("休眠验证使用原 A+B 题包的 1000 ms 墙钟时限")
        output_dir = work / f"wall_{case_id}"
        base_command = baseline["command"]
        command = [*base_command[:3], str(binary), *base_command[4:6], str(output_dir)]
        proc = subprocess.run(command, input=dumps(task, policy).encode("utf-8"),
                              capture_output=True, timeout=10)
        record = {"name": "wall_timeout", "command": command, "task": task, "cli_exit_code": proc.returncode,
                  "cli_stderr": proc.stderr.decode("utf-8", errors="replace")}
        cases.append(record)
        if proc.returncode:
            raise AssertionError(f"{case_id} 未生成超时结果：{record['cli_stderr'].strip()}")
        result = loads(proc.stdout, policy)
        actual = (output_dir / "stdout.out").read_bytes()
        expected = Path(base_command[5]).read_bytes()
        record.update(result=result, actual=repr(actual), expected_prefix=repr(expected))
        if (result["verdict"], result["reason"], result["wall_timeout"], result["signal"]) != (
            "TLE", "WALL_TIMEOUT", True, int(signal.SIGKILL),
        ):
            raise AssertionError(f"{case_id} 应由实际 SIGKILL 确认墙钟超时")
        if result["exit_code"] is not None or result["out_bytes"] is not None or actual != expected:
            raise AssertionError(f"{case_id} 只应保留超时前输出；退出码和未比较的输出长度应为空")
        if (result["wall_ms"] is None or result["wall_ms"] < task["limits"]["wall_ms"]
                or result["cpu_ms"] is None or result["cpu_ms"] >= task["limits"]["cpu_ms"]
                or result["mem_bytes"] is None or result["memory_source"] != "rss_peak"):
            raise AssertionError(f"{case_id} 的实际墙钟、CPU 或 RSS 测量不符")
        for key in ("file_id", "correlation_id", "problem_id", "test_id", "sequence", "attempt", "judge_id"):
            if result[key] != task[key]:
                raise AssertionError(f"{case_id} 的 {key} 不一致")
        if result["node_id"] != task["target_node_id"]:
            raise AssertionError(f"{case_id} 的执行节点不一致")
        record["status"] = "PASSED"
        print(f"测试点 {case_id}：TLE/WALL_TIMEOUT，墙钟={result['wall_ms']} ms，"
              f"CPU={result['cpu_ms']} ms，信号={result['signal']}，超时前输出={actual!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n3_wall_timeout_{uuid4().hex[:12]}"
    work, evidence = root / "work" / run_id, root / "artifacts/system" / run_id
    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/native/eval_task_v1.hpp", "judge/native/eval_task_v1.cpp",
        "judge/native/Makefile", "judge/native/eval_core.hpp", "judge/native/eval_core.cpp",
        "judge/native/eval_local.cpp", "judge/problem.py", "protocol/codec_v1.hpp", "protocol/codec_v1.py",
        "protocol/policy_v1.json", "protocol/golden/eval_task.json", "tests/n3_local_core.py",
        "tests/n3_exit_paths.py", "tests/n3_wall_timeout.py", "examples/a_plus_b.cpp", "examples/wrong_answer.cpp",
        "examples/runtime_error.cpp", "examples/return_125.cpp", "examples/time_limit.cpp",
        "problems/a_plus_b/problem.json",
        *(f"problems/a_plus_b/tests/{case}.{ext}" for case in ("01", "02") for ext in ("in", "out")),
    ]
    manifest = {
        "run_id": run_id, "executed_by": args.actor, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "真实墙钟超时和 AC/WA/RE/SE 回归；不验证 CPU 超限、OOM 或子孙进程清理",
        "source_sha256": {name: sha256(root / name) for name in inputs}, "builds": [], "guards": [],
    }
    cases = []
    try:
        verify_ac_wa(root, work, evidence, run_id, manifest, cases)
        verify_exit_paths(root, work, evidence, run_id, manifest, cases)
        verify_wall_timeout(root, work, evidence, run_id, manifest, cases)
        manifest.update(status="PASSED", exit_code=0, verified_cases=len(cases))
        print("N3.1 墙钟超时验证通过：两点均为 TLE/WALL_TIMEOUT，AC/WA/RE/SE 回归通过")
    except Exception as error:
        manifest.update(status="FAILED", exit_code=1, error=str(error))
        print(f"N3.1 墙钟超时验证失败：{error}")
    write_json(evidence / "cases.json", cases)
    write_json(evidence / "manifest.json", manifest)
    print("运行证据：", evidence.relative_to(root))
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
