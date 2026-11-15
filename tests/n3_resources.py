"""N3.1 资源边界：真实 CPU/地址空间执行与单独标记的合成内存判定。"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess

from protocol.codec_v1 import dumps, loads
from tests.n3_local_core import sha256


class ResourceUnavailable(RuntimeError):
    """本机容量不足，保留未验证状态。"""


def require_cpus(manifest, minimum):
    capacity = float(len(os.sched_getaffinity(0)))
    cgroup_root = Path("/sys/fs/cgroup")
    try:
        line = next(line for line in Path("/proc/self/cgroup").read_text().splitlines() if line.startswith("0::"))
        current = cgroup_root / line[3:].lstrip("/")
        for directory in (current, *current.parents):
            if directory != cgroup_root and cgroup_root not in directory.parents:
                break
            config = directory / "cpu.max"
            if config.is_file():
                quota, period = config.read_text().split()
                if quota != "max":
                    capacity = min(capacity, int(quota) / int(period))
    except (OSError, StopIteration, ValueError):
        pass  # 不可读时仍保留 affinity；实跑结果负责最终确认。
    manifest["available_cpu_capacity"] = capacity
    if capacity < minimum:
        raise ResourceUnavailable(f"至少需要 {minimum} 个可用 CPU，本机约 {capacity:g} 个")


def build(command, evidence, manifest, timeout=30):
    record = {"command": command}
    manifest["builds"].append(record)
    with (evidence / "build.txt").open("a", encoding="utf-8") as log:
        print(json.dumps(command, ensure_ascii=False), file=log, flush=True)
        proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    record["exit_code"] = proc.returncode
    if proc.returncode:
        raise RuntimeError("资源验证构建失败，见 build.txt")


def compile_sample(root, work, evidence, manifest, source, target, extra_flags=()):
    policy = json.loads((root / "protocol/policy_v1.json").read_text(encoding="utf-8"))
    profile = policy["compiler_profiles"]["cpp_baseline_v1"]
    binary = work / target
    command = [profile["compiler"], *profile["flags"], *extra_flags, str(root / "examples" / source), "-o", str(binary)]
    build(command, evidence, manifest, timeout=profile["limits"]["wall_ms"] / 1000)
    return binary, policy


def run_case(work, run_id, cases, baseline, policy, binary, label, enqueue_seq, **limit_changes):
    original = baseline["task"]
    file_id = f"{run_id}_{label}"
    judge_id = f"{file_id}:{original['test_id']}:1"
    task = dict(original, file_id=file_id, correlation_id=file_id, judge_id=judge_id,
                msg_id=f"task:{judge_id}", enqueue_seq=enqueue_seq, bin_hash="sha256:" + sha256(binary),
                limits=dict(original["limits"], **limit_changes),
                emitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
    output_dir = work / label
    base_command = baseline["command"]
    command = [*base_command[:3], str(binary), *base_command[4:6], str(output_dir)]
    proc = subprocess.run(command, input=dumps(task, policy).encode("utf-8"), capture_output=True, timeout=10)
    record = {"name": label, "command": command, "task": task, "cli_exit_code": proc.returncode,
              "cli_stderr": proc.stderr.decode("utf-8", errors="replace")}
    cases.append(record)
    if proc.returncode:
        raise AssertionError(f"{label} 未生成结果：{record['cli_stderr'].strip()}")
    result = loads(proc.stdout, policy)
    actual = (output_dir / "stdout.out").read_bytes()
    stderr = (output_dir / "stderr.err").read_bytes()
    record.update(result=result, actual=repr(actual), program_stderr=repr(stderr))
    for key in ("file_id", "correlation_id", "problem_id", "test_id", "sequence", "attempt", "judge_id"):
        if result[key] != task[key]:
            raise AssertionError(f"{label} 的 {key} 不一致")
    if result["node_id"] != task["target_node_id"] or result["memory_source"] != "rss_peak":
        raise AssertionError(f"{label} 执行节点或内存口径不符")
    return record, result, actual, stderr


def check_cpu_result(result, expected_signal, expected_exit):
    if (result["verdict"], result["reason"], result["wall_timeout"], result["signal"], result["exit_code"]) != (
        "TLE", "CPU_TIMEOUT", False, expected_signal, expected_exit,
    ) or result["out_bytes"] is not None or result["cpu_ms"] is None:
        raise AssertionError(f"CPU 路径判定不符：{result['verdict']}/{result['reason']}，信号={result['signal']}")


def verify_cpu(root, work, evidence, run_id, manifest, cases):
    require_cpus(manifest, 2)
    binary, policy = compile_sample(root, work, evidence, manifest, "cpu_limit.cpp", "cpu_limit", ("-pthread",))
    record, result, actual, _ = run_case(work, run_id, cases, cases[0], policy, binary, "cpu_soft", 9)
    check_cpu_result(result, int(signal.SIGXCPU), None)
    if actual:
        raise AssertionError("CPU 软限制用例不应执行到输出")
    record["status"] = "PASSED"
    print(f"CPU 软限制：TLE/CPU_TIMEOUT，SIGXCPU，CPU={result['cpu_ms']} ms，墙钟={result['wall_ms']} ms")

    record, result, actual, _ = run_case(work, run_id, cases, cases[1], policy, binary, "cpu_measured", 10,
                                         cpu_ms=450, wall_ms=450)
    check_cpu_result(result, 0, 0)
    if not 450 < result["cpu_ms"] < 1000 or actual != b"-5\n":
        raise AssertionError("程序应在内核秒级上限前退出，但实测 CPU 超过 450 ms")
    record["status"] = "PASSED"
    print(f"CPU 实测超限：TLE/CPU_TIMEOUT，退出 0，CPU={result['cpu_ms']} ms，上限 450 ms")


def verify_cpu_hard(root, work, evidence, run_id, manifest, cases):
    require_cpus(manifest, 3)
    binary, policy = compile_sample(root, work, evidence, manifest, "cpu_limit.cpp", "cpu_hard_limit",
                                    ("-pthread", "-DALGOOS_TEST_CPU_HARD_LIMIT"))
    record, result, actual, _ = run_case(work, run_id, cases, cases[0], policy, binary, "cpu_hard", 11)
    check_cpu_result(result, int(signal.SIGKILL), None)
    if actual or result["cpu_ms"] < 1900:
        raise AssertionError("CPU 硬限制用例应消耗约 2 秒 CPU 后终止")
    record["status"] = "PASSED"
    print(f"CPU 硬限制：TLE/CPU_TIMEOUT，SIGKILL，CPU={result['cpu_ms']} ms，墙钟={result['wall_ms']} ms")


def verify_memory(root, work, evidence, run_id, manifest, cases):
    binary, policy = compile_sample(root, work, evidence, manifest, "memory_limit.cpp", "memory_limit")
    record, result, actual, stderr = run_case(work, run_id, cases, cases[0], policy, binary, "as_refused", 12,
                                              mem_bytes=64 << 20)
    marker = b"RLIMIT_AS=67108864 mmap(134217728)=ENOMEM\n"
    if (result["verdict"], result["reason"], result["exit_code"], result["signal"], result["wall_timeout"]) != (
        "RE", "NONZERO_EXIT", 7, 0, False,
    ) or actual != b"3\n" or stderr != marker or result["stderr_tail"] != marker.decode():
        raise AssertionError("地址空间拒绝证据或非零退出判定不符，不能仅凭 ENOMEM 记 MLE")
    record["status"] = "PASSED"
    print("地址空间限制：64 MiB 上限拒绝 128 MiB 映射；程序返回 7 判 RE，保留 ENOMEM 摘要")

    record, result, actual, stderr = run_case(work, run_id, cases, cases[1], policy, binary, "rss_and_stderr", 13,
                                              mem_bytes=64 << 20)
    if (result["verdict"], result["reason"], result["exit_code"], result["signal"]) != ("AC", "NONE", 0, 0):
        raise AssertionError("限内内存用例应正常通过")
    if actual != b"-5\n" or result["mem_bytes"] is None or not (8 << 20) <= result["mem_bytes"] <= (64 << 20):
        raise AssertionError("限内 RSS 测量或答案不符")
    if stderr != b"\xe4\xb8\xad" + b"x" * 4095 or result["stderr_tail"] != "\\xad" + "x" * 4095:
        raise AssertionError("错误摘要应按末尾 4096 个原始字节截取，转义截断的 UTF-8")
    record["status"] = "PASSED"
    print(f"限内内存：AC，RSS 峰值={result['mem_bytes']} 字节；诊断截尾和无效 UTF-8 转义通过")


def verify_resource_decisions(root, work, evidence, run_id, manifest, cases):
    binary = work / "resource_decisions"
    command = ["g++", "-std=c++23", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-pthread",
               str(root / "judge/native/eval_core.cpp"), str(root / "tests/n3_resource_decisions.cpp"), "-o", str(binary)]
    build(command, evidence, manifest, timeout=120)
    proc = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    manifest["resource_decisions"] = {"data_source": "synthetic", "command": [str(binary)],
                                      "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    if proc.returncode or proc.stdout.strip() != "7 synthetic resource decisions passed":
        raise AssertionError(f"合成判定检查失败：{proc.stderr.strip()}")
    manifest["synthetic_checks"] = 7
    print("7 项合成证据通过：内存边界、判定优先级、单独 SIGKILL 不认作 OOM")
