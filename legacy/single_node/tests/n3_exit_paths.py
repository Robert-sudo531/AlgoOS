"""N3.1：区分程序返回非零值和执行器启动失败，回归 AC/WA。"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from protocol.codec_v1 import dumps, loads
from tests.n3_local_core import sha256, verify as verify_ac_wa, write_json


def verify_exit_paths(root, work, evidence, run_id, manifest, cases):
    baseline = cases[0]  # 原 A+B 的正确程序，测试点 01。
    base_command = baseline["command"]
    policy = json.loads(Path(base_command[1]).read_text(encoding="utf-8"))
    profile = policy["compiler_profiles"]["cpp_baseline_v1"]
    expected = Path(base_command[5]).read_bytes()

    with (evidence / "build.txt").open("a", encoding="utf-8") as log:
        for label, source in (("exit_7", "runtime_error.cpp"), ("exit_125", "return_125.cpp")):
            command = [profile["compiler"], *profile["flags"], str(root / "examples" / source),
                       "-o", str(work / label)]
            print(json.dumps(command, ensure_ascii=False), file=log, flush=True)
            proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                  timeout=profile["limits"]["wall_ms"] / 1000)
            manifest["builds"].append({"command": command, "exit_code": proc.returncode})
            if proc.returncode:
                raise RuntimeError(f"{label} 编译失败，见 build.txt")

    # 普通文件可通过路径检查，但不是可执行格式，真实触发 execv 失败。
    invalid = work / "execv_failure"
    invalid.write_bytes(b"AlgoOS test fixture: not an executable\n")
    invalid.chmod(0o755)

    for enqueue_seq, (label, verdict, reason, exit_code) in enumerate((
        ("exit_7", "RE", "NONZERO_EXIT", 7),
        ("exit_125", "RE", "NONZERO_EXIT", 125),
        ("execv_failure", "SE", "SYSTEM_ERROR", None),
    ), 3):
        binary = work / label
        file_id = f"{run_id}_{label}"
        judge_id = f"{file_id}:01:1"
        task = dict(baseline["task"], file_id=file_id, correlation_id=file_id, judge_id=judge_id,
                    msg_id=f"task:{judge_id}", enqueue_seq=enqueue_seq, bin_hash="sha256:" + sha256(binary),
                    emitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
        output_dir = work / f"{label}_01"
        command = [*base_command[:3], str(binary), *base_command[4:6], str(output_dir)]
        proc = subprocess.run(command, input=dumps(task, policy).encode("utf-8"),
                              capture_output=True, timeout=10)
        record = {"name": label, "command": command, "task": task, "cli_exit_code": proc.returncode,
                  "cli_stderr": proc.stderr.decode("utf-8", errors="replace")}
        cases.append(record)
        if proc.returncode:
            raise AssertionError(f"{label} 未生成评测结果：{record['cli_stderr'].strip()}")
        result = loads(proc.stdout, policy)
        stdout = (output_dir / "stdout.out").read_bytes()
        stderr = (output_dir / "stderr.err").read_bytes()
        record.update(result=result, actual=repr(stdout), program_stderr=repr(stderr))
        if (result["verdict"], result["reason"], result["exit_code"]) != (verdict, reason, exit_code):
            raise AssertionError(f"{label} 判定、原因或程序退出码不符")
        for key in ("file_id", "correlation_id", "problem_id", "test_id", "sequence", "attempt", "judge_id"):
            if result[key] != task[key]:
                raise AssertionError(f"{label} 的 {key} 不一致")
        if result["node_id"] != task["target_node_id"] or result["out_bytes"] is not None or result["wall_timeout"]:
            raise AssertionError(f"{label} 节点、输出长度或超时字段不符")
        if verdict == "RE":
            if (stdout != expected or result["signal"] != 0 or result["memory_source"] != "rss_peak"
                    or any(result[key] is None for key in ("cpu_ms", "wall_ms", "mem_bytes"))):
                raise AssertionError(f"{label} 应产生正确输出及实际执行测量")
            print(f"程序返回 {exit_code}：RE/NONZERO_EXIT，实际输出={stdout!r}")
        else:
            absent = ("exit_code", "signal", "cpu_ms", "wall_ms", "mem_bytes", "memory_source")
            if stdout or stderr or any(result[key] is not None for key in absent) or not result["detail"].startswith("execv:"):
                raise AssertionError("启动失败应有 execv 证据，且不填写被测程序退出码和测量值")
            print(f"启动失败：SE/SYSTEM_ERROR，程序退出码=null，原因={result['detail']}")
        record["status"] = "PASSED"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n3_exit_paths_{uuid4().hex[:12]}"
    work, evidence = root / "work" / run_id, root / "artifacts/system" / run_id
    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/native/eval_task_v1.hpp", "judge/native/eval_task_v1.cpp",
        "judge/native/Makefile", "judge/native/eval_core.hpp", "judge/native/eval_core.cpp",
        "judge/native/eval_local.cpp", "judge/problem.py", "protocol/codec_v1.hpp", "protocol/codec_v1.py",
        "protocol/policy_v1.json", "protocol/golden/eval_task.json", "tests/n3_local_core.py", "tests/n3_exit_paths.py",
        "examples/a_plus_b.cpp", "examples/wrong_answer.cpp", "examples/runtime_error.cpp", "examples/return_125.cpp",
        "problems/a_plus_b/problem.json",
        *(f"problems/a_plus_b/tests/{case}.{ext}" for case in ("01", "02") for ext in ("in", "out")),
    ]
    manifest = {
        "run_id": run_id, "executed_by": args.actor, "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "AC/WA 回归及实际非零退出、execv 启动失败；不验证信号、超时或资源超限判定",
        "source_sha256": {name: sha256(root / name) for name in inputs}, "builds": [], "guards": [],
    }
    cases = []
    try:
        verify_ac_wa(root, work, evidence, run_id, manifest, cases)
        verify_exit_paths(root, work, evidence, run_id, manifest, cases)
        manifest.update(status="PASSED", exit_code=0, verified_cases=len(cases))
        print("N3.1 RE/SE 区分验证通过：退出 7、125 均判 RE，启动失败判 SE；AC/WA 回归通过")
    except Exception as error:
        manifest.update(status="FAILED", exit_code=1, error=str(error))
        print(f"N3.1 RE/SE 区分验证失败：{error}")
    write_json(evidence / "cases.json", cases)
    write_json(evidence / "manifest.json", manifest)
    print("运行证据：", evidence.relative_to(root))
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
