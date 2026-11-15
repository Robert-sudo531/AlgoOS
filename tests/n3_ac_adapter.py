"""N3.1 第一步：真实执行 A+B，验证 AC 结果转换及 v1 契约。"""

import argparse
import contextlib
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from uuid import uuid4

from judge.pipeline import Pipeline
from judge.problem import trim_common
from judge.result_adapter import adapt_ac_result
from protocol.codec_v1 import ContractError, dumps, loads
from tests.n1_wa import CaptureClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明", help="实际执行人，例如 lhy 或 AI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n3_ac_adapter_{uuid4().hex[:12]}"
    work, evidence = root / "work" / run_id, root / "artifacts/system" / run_id
    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/pipeline.py", "judge/problem.py", "judge/result_adapter.py",
        "judge/native/eval_server.cpp", "judge/native/Makefile",
        "protocol/codec_v1.py", "protocol/policy_v1.json", "tests/n1_wa.py",
        "tests/n3_ac_adapter.py", "examples/a_plus_b.cpp", "problems/a_plus_b/problem.json",
        "problems/a_plus_b/tests/01.in", "problems/a_plus_b/tests/01.out",
        "problems/a_plus_b/tests/02.in", "problems/a_plus_b/tests/02.out",
    ]
    manifest = {
        "run_id": run_id, "executed_by": args.actor,
        "started_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0],
        "scope": "N3.1 第一步：旧 C++ 核心真实 AC→v1 结果；非分布式 Worker，不验证其他判定",
        "status": "RUNNING",
        "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in inputs},
    }
    exit_code = 1
    with (evidence / "verify.txt").open("x", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                command = ["make", "-C", str(root / "judge/native"), f"BUILD_DIR={work / 'native'}"]
                manifest["build_command"] = command
                with (evidence / "build.txt").open("x", encoding="utf-8") as build_log:
                    build = subprocess.run(command, stdout=build_log, stderr=subprocess.STDOUT, timeout=120)
                manifest["build_exit_code"] = build.returncode
                if build.returncode:
                    raise RuntimeError("构建失败，见 build.txt")
                submissions = work / "submissions"
                submissions.mkdir()
                shutil.copyfile(root / "examples/a_plus_b.cpp", submissions / "0001__a_plus_b.cpp")
                pipeline = Pipeline(root / "problems", submissions, evidence, work / "native/eval_server", workers=1)
                try:
                    pipeline.start_server()
                    captured = CaptureClient(pipeline.client)
                    pipeline.client = captured
                    pipeline.process_existing()
                finally:
                    pipeline.stop_server()
                with (evidence / "result.tsv").open(encoding="utf-8", newline="") as result_file:
                    rows = list(csv.DictReader(result_file, delimiter="\t"))
                if len(rows) != 1 or (rows[0]["verdict"], rows[0]["passed"], rows[0]["total"]) != ("AC", "2", "2"):
                    raise AssertionError("真实基线没有得到 AC 2/2")
                (evidence / "responses.json").write_text(
                    json.dumps(captured.results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                requests = {item["test_id"]: item for item in captured.requests}
                results = {item["test_id"]: item for item in captured.results}
                if len(captured.results) != 2 or set(results) != {"01", "02"}:
                    raise AssertionError("测试点结果数量或编号不符")
                policy = json.loads((root / "protocol/policy_v1.json").read_text(encoding="utf-8"))
                messages = []
                for sequence, case in enumerate(pipeline.problems["a_plus_b"].tests, 1):
                    raw, request = results[case.case_id], requests[case.case_id]
                    message = adapt_ac_result(request, raw, sequence=sequence, attempt=1,
                                              node_id="local-eval-01", policy=policy)
                    actual = (submissions / "0001__a_plus_b__result" / f"{case.case_id}.out").read_bytes()
                    if actual != case.output_path.read_bytes() or len(trim_common(actual)) != message["out_bytes"]:
                        raise AssertionError("真实输出或输出长度不符")
                    if (message["test_id"] != case.case_id or message["sequence"] != sequence
                            or message["cpu_ms"] != raw["cpu_ms"] or message["wall_ms"] is not None
                            or message["mem_bytes"] != raw["mem_kb"] * 1024
                            or message["memory_source"] != "rss_peak"):
                        raise AssertionError("身份、计量单位或空值映射错误")
                    if loads(dumps(message, policy), policy) != message:
                        raise AssertionError("真实结果未通过 v1 往返检查")
                    messages.append(message)
                    (evidence / f"{case.case_id}.actual.out").write_bytes(actual)
                    print(f"测试点 {case.case_id}：AC/NONE，退出码 {message['exit_code']}，实际={actual!r}")
                (evidence / "v1_results.json").write_text(
                    json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                # 防止把不支持的结果或另一测试点的结果包装成 AC。
                first = pipeline.problems["a_plus_b"].tests[0].case_id
                for key, value, expected_error in [
                    ("verdict", "WA", NotImplementedError),
                    ("test_id", "another-case", ContractError),
                ]:
                    wrong = copy.deepcopy(results[first])
                    wrong[key] = value
                    try:
                        adapt_ac_result(requests[first], wrong, sequence=1, attempt=1,
                                        node_id="local-eval-01", policy=policy)
                    except expected_error:
                        continue
                    raise AssertionError("适配层没有拒绝错误身份或不支持的判定")
                print("v1 契约检查通过；非 AC 与身份不一致的响应已拒绝")
                print("N3.1 第一步验证通过：真实 AC 2/2 → v1 结果")
                manifest.update(status="PASSED", adapted_cases=2, guard_checks=2)
                exit_code = 0
            except Exception as error:
                traceback.print_exc()
                manifest.update(status="FAILED", error=str(error))
    manifest["exit_code"] = exit_code
    (evidence / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print((evidence / "verify.txt").read_text(encoding="utf-8"), end="")
    print("运行证据：", evidence.relative_to(root))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
