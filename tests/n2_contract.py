"""N2.4：编译 C++ 检查入口，验证 Python/C++ 消息往返和错误拒绝。"""

import argparse
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import traceback
from uuid import uuid4

from protocol.codec_v1 import ContractError, dumps, loads


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cases(fixtures):
    valid = [(name, encoded(value)) for name, value in fixtures.items()]
    invalid = []

    def variant(name, base, changes, *, accepted=False):
        message = copy.deepcopy(fixtures[base])
        for path, value in changes:
            keys = path.split(".")
            parent = message
            for key in keys[:-1]:
                parent = parent[key]
            if value is DELETE:
                del parent[keys[-1]]
            else:
                parent[keys[-1]] = value
        (valid if accepted else invalid).append((name, encoded(message)))

    task, result, success, failure = "eval_task", "eval_result", "compile_success_result", "compile_error_result"
    DELETE = object()
    variant("附加字段与整数边界保留", task, [
        ("extension", {"中文": [None, True, False, 0, -3, 2**63 - 1, -(2**63),
                                0.125, "😀", "a\0b", {"unit": "bytes"}]}),
        ("limits.extension", "bytes")], accepted=True)
    variant("零输出上限", task, [("limits.out_bytes", 0)], accepted=True)
    variant("显式 FINAL", result, [("status", "FINAL")], accepted=True)
    variant("未知测量值保留 null", result, [("out_bytes", None)], accepted=True)
    variant("UTC 小数秒与闰年", task, [("emitted_at", "2024-02-29T01:02:03.123456Z")], accepted=True)
    variant("系统错误优先但保留墙钟证据", result,
            [("verdict", "SE"), ("reason", "SYSTEM_ERROR"), ("wall_timeout", True)], accepted=True)
    for verdict, reason in [
        ("AC", "NONE"), ("WA", "OUTPUT_LIMIT"), ("RE", "NONZERO_EXIT"),
        ("RE", "CRASH_SIGNAL"), ("TLE", "WALL_TIMEOUT"), ("TLE", "CPU_TIMEOUT"),
        ("MLE", "OOM_KILL"), ("MLE", "MEM_PEAK_OVER"), ("SE", "SYSTEM_ERROR"),
    ]:
        signal = {"CRASH_SIGNAL": 11, "WALL_TIMEOUT": 9, "CPU_TIMEOUT": 24, "OOM_KILL": 9}.get(reason, 0)
        variant(f"分支 {verdict}/{reason}", result, [
            ("verdict", verdict), ("reason", reason), ("signal", signal),
            ("exit_code", None if signal else (7 if verdict == "RE" else 0)),
            ("wall_timeout", reason == "WALL_TIMEOUT"),
            ("cpu_ms", 3), ("wall_ms", 5), ("mem_bytes", 4096), ("memory_source", "rss_peak")
        ], accepted=True)
    for verdict, reason in [("CE", "COMPILE_TIMEOUT"), ("CE", "COMPILE_LIMIT"), ("SE", "SYSTEM_ERROR")]:
        variant(f"编译分支 {verdict}/{reason}", failure,
                [("verdict", verdict), ("reason", reason)], accepted=True)

    for name, base, changes in [
        ("缺少版本", task, [("ver", DELETE)]),
        ("缺少 limits", task, [("limits", DELETE)]),
        ("缺少测试点", task, [("test_id", DELETE)]),
        ("缺少测量字段", result, [("cpu_ms", DELETE)]),
        ("缺少成功产物", success, [("bin_hash", DELETE)]),
        ("缺少编译空身份字段", failure, [("judge_id", DELETE)]),
        ("不支持版本", task, [("ver", 2)]),
        ("不支持消息种类", task, [("type", "cancel_task")]),
        ("布尔值冒充版本", task, [("ver", True)]),
        ("浮点数冒充序号", task, [("sequence", 1.0)]),
        ("数字丢失前导零", task, [("test_id", 1)]),
        ("零 attempt", task, [("attempt", 0)]),
        ("负测量值", result, [("cpu_ms", -1)]),
        ("布尔值冒充内存", task, [("limits.mem_bytes", True)]),
        ("负输出上限", task, [("limits.out_bytes", -1)]),
        ("零 CPU 上限", task, [("limits.cpu_ms", 0)]),
        ("超大整数", task, [("enqueue_seq", 2**63)]),
        ("负整数下溢附加字段", task, [("extension", -(2**63) - 1)]),
        ("correlation 不一致", task, [("correlation_id", "other")]),
        ("judge_id 不一致", task, [("attempt", 2)]),
        ("test_id 含冒号", task, [("test_id", "a:b")]),
        ("node_id 破坏路由", task, [("target_node_id", "eval.01")]),
        ("哈希长度错误", task, [("bin_hash", "sha256:123")]),
        ("哈希大写", task, [("bin_hash", "sha256:" + "A" * 64)]),
        ("不支持模式", task, [("compare_mode", "special")]),
        ("不支持编译配置", "compile_task", [("compiler_profile", "cpp_latest")]),
        ("编译配置含 NUL", "compile_task", [("compiler_profile", "cpp_baseline_v1\0suffix")]),
        ("客户端改写编译限制", "compile_task", [("limits.mem_bytes", 1)]),
        ("墙钟配置不同", task, [("limits.wall_ms", 2000)]),
        ("时间无 UTC 标记", task, [("emitted_at", "2026-11-10T00:00:00+08:00")]),
        ("日期不存在", task, [("emitted_at", "2026-02-29T00:00:00Z")]),
        ("错误阶段来源", result, [("source", "compiler")]),
        ("判定原因冲突", result, [("reason", "NONE")]),
        ("未定义原因", result, [("reason", "UNKNOWN")]),
        ("评测伪造 CE", result, [("verdict", "CE"), ("reason", "COMPILE_ERROR")]),
        ("编译伪造测试点", failure, [("test_id", "01")]),
        ("编译伪造程序退出码", failure, [("exit_code", 1)]),
        ("COMPILED 携带终态判定", success, [("verdict", "AC")]),
        ("编译失败伪装成功", failure, [("status", "COMPILED")]),
        ("测试点伪装 COMPILED", result, [("status", "COMPILED")]),
        ("未知状态", result, [("status", "DONE")]),
        ("null 状态不是缺省", result, [("status", None)]),
        ("信号与退出码冲突", result, [("signal", 9)]),
        ("整数冒充布尔值", result, [("wall_timeout", 0)]),
        ("墙钟证据不一致", result, [("wall_timeout", True)]),
        ("短路不在首轮范围", result, [("short_circuited", True)]),
        ("内存未给口径", result, [("mem_bytes", 4096)]),
        ("内存口径不支持", result, [("mem_bytes", 4096), ("memory_source", "as_limit")]),
    ]:
        variant(name, base, changes)
    raw = encoded(fixtures[task])
    invalid.extend([
        ("损坏 JSON", raw[:-1]), ("JSON 后多一条对象", raw + b"{}"),
        ("重复版本键", b'{"ver":2,' + raw[1:]),
        ("NaN 附加字段", raw[:-1] + b',"extension":NaN}'),
        ("Infinity 附加字段", raw[:-1] + b',"extension":1e999}'),
        ("无效 UTF-8", raw[:-1] + b',"extension":"\xff"}'),
        ("孤立 Unicode 代理项", raw[:-1] + br',"extension":"\ud800"}'),
        ("键包含 NUL", raw[:-1] + br',"a\u0000b":0}'),
        ("UTF-16 不是线格式", raw.decode().encode("utf-16")),
        ("根节点不是对象", b"[]"),
    ])
    return valid, invalid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明", help="实际执行人，例如 lhy 或 AI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n2_contract_{uuid4().hex[:12]}"
    work = root / "work" / run_id
    evidence = root / "artifacts/system" / run_id
    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    sources = sorted([*root.glob("protocol/golden/*.json"), root / "protocol/policy_v1.json",
                      root / "protocol/__init__.py", root / "protocol/codec_v1.py",
                      root / "protocol/codec_v1.hpp", root / "protocol/contract_check.cpp", Path(__file__)])
    manifest = {
        "run_id": run_id, "executed_by": args.actor,
        "started_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0],
        "scope": "N2.4 合成消息契约；未连接队列、执行被测程序或验证资源限制",
        "status": "RUNNING",
        "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    }
    checks, exit_code = [], 1
    with (evidence / "verify.txt").open("x", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                policy_path = root / "protocol/policy_v1.json"
                policy = json.loads(policy_path.read_text(encoding="utf-8"))
                fixtures = {p.stem: json.loads(p.read_text(encoding="utf-8"))
                            for p in sorted((root / "protocol/golden").glob("*.json"))}
                package = subprocess.run(["pkg-config", "--cflags", "--libs", "json-c"],
                                         capture_output=True, text=True, timeout=10, check=True)
                binary = work / "contract_check"
                build_command = ["g++", "-std=c++23", "-O2", "-pipe", "-Wall", "-Wextra", "-Wpedantic",
                                 str(root / "protocol/contract_check.cpp"), "-o", str(binary),
                                 *shlex.split(package.stdout)]
                manifest["build_command"] = build_command
                with (evidence / "build.txt").open("x", encoding="utf-8") as build_log:
                    build = subprocess.run(build_command, stdout=build_log, stderr=subprocess.STDOUT, timeout=120)
                manifest["build_exit_code"] = build.returncode
                if build.returncode:
                    raise RuntimeError("C++ 检查入口编译失败，见 build.txt")
                print("C++ 契约检查入口编译通过")

                def cpp(payload):
                    return subprocess.run([str(binary), str(policy_path)], input=payload,
                                          capture_output=True, timeout=5)

                valid, invalid = cases(fixtures)
                for name, payload in valid:
                    record = {"name": name, "expected": "ACCEPT", "input": payload.decode("utf-8")}
                    checks.append(record)
                    python_message = loads(payload, policy)
                    # 两个方向都检查全部字段，不能只检查预先挑选的几个编号。
                    for direction, wire in [("Python→C++→Python", dumps(python_message, policy).encode()),
                                            ("C++→Python→C++", payload)]:
                        response = cpp(wire)
                        record[direction] = {"exit_code": response.returncode,
                                             "stderr": response.stderr.decode("utf-8", errors="replace")}
                        if response.returncode != 0:
                            raise AssertionError(f"{name} / {direction}：C++ 拒绝合法消息")
                        returned = loads(response.stdout, policy)
                        if canonical(returned) != canonical(python_message):
                            raise AssertionError(f"{name} / {direction}：字段发生变化")
                        if direction == "C++→Python→C++":
                            again = cpp(dumps(returned, policy).encode())
                            if again.returncode or canonical(loads(again.stdout, policy)) != canonical(returned):
                                raise AssertionError(f"{name}：反向往返不一致")
                    record["status"] = "PASSED"
                print(f"合法消息双向往返：{len(valid)} 项通过（含 5 份原样例）")
                for name, payload in invalid:
                    record = {"name": name, "expected": "REJECT", "input_hex": payload.hex()}
                    checks.append(record)
                    try:
                        loads(payload, policy)
                    except ContractError as error:
                        record["python_error"] = str(error)
                    else:
                        raise AssertionError(f"{name}：Python 未拒绝错误输入")
                    response = cpp(payload)
                    record["cpp_exit_code"] = response.returncode
                    record["cpp_error"] = response.stderr.decode("utf-8", errors="replace")
                    if response.returncode != 1 or response.stdout or not response.stderr:
                        raise AssertionError(f"{name}：C++ 未正常拒绝错误输入")
                    record["status"] = "PASSED"
                print(f"错误消息双端拒绝：{len(invalid)} 项通过")
                manifest.update(status="PASSED", valid_cases=len(valid), invalid_cases=len(invalid))
                print("N2.4 Python/C++ 契约验证通过")
                exit_code = 0
            except Exception as error:
                traceback.print_exc()
                manifest.update(status="FAILED", error=str(error))
    (evidence / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["exit_code"] = exit_code
    (evidence / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print((evidence / "verify.txt").read_text(encoding="utf-8"), end="")
    print("运行证据：", evidence.relative_to(root))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
