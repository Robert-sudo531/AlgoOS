"""N3.1：真实错误答案 WA→v1 ANSWER_MISMATCH，保存输出与适配证据。"""

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
from judge.result_adapter import adapt_wa_mismatch_result
from protocol.codec_v1 import ContractError, dumps, loads
from tests.n1_wa import CaptureClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明", help="实际执行人，例如 lhy 或 AI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n3_wa_adapter_{uuid4().hex[:12]}"
    work, evidence = root / "work" / run_id, root / "artifacts/system" / run_id
    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/pipeline.py", "judge/problem.py", "judge/result_adapter.py",
        "judge/native/eval_server.cpp", "judge/native/Makefile", "protocol/codec_v1.py",
        "protocol/policy_v1.json", "tests/n1_wa.py", "tests/n3_wa_adapter.py",
        "examples/wrong_answer.cpp", "problems/a_plus_b/problem.json",
        "problems/a_plus_b/tests/01.in", "problems/a_plus_b/tests/01.out",
        "problems/a_plus_b/tests/02.in", "problems/a_plus_b/tests/02.out",
    ]
    manifest = {
        "run_id": run_id, "executed_by": args.actor,
        "started_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version.split()[0],
        "scope": "真实 WA 内容错误→v1；另有合成防误用检查，未实测原生输出超限路径",
        "status": "RUNNING",
        "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in inputs},
    }
    exit_code = 1
    guards = []
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
                shutil.copyfile(root / "examples/wrong_answer.cpp", submissions / "0002__a_plus_b.cpp")
                pipeline = Pipeline(root / "problems", submissions, evidence, work / "native/eval_server", workers=1)
                try:
                    pipeline.start_server()
                    captured = CaptureClient(pipeline.client)
                    pipeline.client = captured
                    pipeline.process_existing()
                finally:
                    pipeline.stop_server()
                (evidence / "responses.json").write_text(
                    json.dumps(captured.results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                with (evidence / "result.tsv").open(encoding="utf-8", newline="") as stream:
                    rows = list(csv.DictReader(stream, delimiter="\t"))
                if len(rows) != 1 or (rows[0]["verdict"], rows[0]["passed"], rows[0]["total"]) != ("WA", "0", "2"):
                    raise AssertionError("真实基线没有得到 WA 0/2")
                requests = {item["test_id"]: item for item in captured.requests}
                results = {item["test_id"]: item for item in captured.results}
                if len(captured.results) != 2 or set(results) != {"01", "02"}:
                    raise AssertionError("测试点响应数量或编号不符")
                policy = json.loads((root / "protocol/policy_v1.json").read_text(encoding="utf-8"))
                messages = []
                expected_wrong = {"01": b"1\n", "02": b"11\n"}
                for sequence, case in enumerate(pipeline.problems["a_plus_b"].tests, 1):
                    request, raw = requests[case.case_id], results[case.case_id]
                    message = adapt_wa_mismatch_result(request, raw, sequence=sequence, attempt=1,
                                                       node_id="local-eval-01", policy=policy)
                    actual = (submissions / "0002__a_plus_b__result" / f"{case.case_id}.out").read_bytes()
                    expected = case.output_path.read_bytes()
                    if (actual != expected_wrong[case.case_id] or trim_common(actual) == trim_common(expected)
                            or len(trim_common(actual)) > request["limits"]["out_bytes"]):
                        raise AssertionError("本样例应为输出未超限的内容错误")
                    if (message["test_id"] != case.case_id or message["sequence"] != sequence
                            or message["verdict"] != "WA" or message["reason"] != "ANSWER_MISMATCH"
                            or message["out_bytes"] != len(trim_common(actual))
                            or message["cpu_ms"] != raw["cpu_ms"] or message["wall_ms"] is not None
                            or message["mem_bytes"] != raw["mem_kb"] * 1024
                            or message["memory_source"] != "rss_peak"):
                        raise AssertionError("v1 判定、身份或测量映射错误")
                    if loads(dumps(message, policy), policy) != message:
                        raise AssertionError("WA 结果未通过 v1 往返检查")
                    messages.append(message)
                    (evidence / f"{case.case_id}.actual.out").write_bytes(actual)
                    print(f"测试点 {case.case_id}：WA/ANSWER_MISMATCH，退出码 0，实际={actual!r}，预期={expected!r}，输出未超限")
                (evidence / "v1_results.json").write_text(
                    json.dumps(messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

                # 以下只测试适配层拒绝行为，明确记录为合成输入，不冒充原生执行结果。
                variants = [
                    ("身份不一致", {"test_id": "another-case"}, None, ContractError),
                    ("信号终止不是内容错误", {"signal": 25, "exit_code": -1}, None, ContractError),
                    ("输出超限不能标为内容错误", {"out_bytes": 3}, b"123\n", NotImplementedError),
                    ("实际输出符合答案", {}, b"3\n", ContractError),
                    ("输出计数与文件不符", {"out_bytes": 0}, None, ContractError),
                ]
                for index, (name, changes, output, expected_error) in enumerate(variants, 1):
                    request, response = copy.deepcopy(requests["01"]), copy.deepcopy(results["01"])
                    response.update(changes)
                    if output is not None:
                        guard_dir = work / f"guard_{index}"
                        guard_dir.mkdir()
                        (guard_dir / "01.out").write_bytes(output)
                        request["result_dir"] = str(guard_dir)
                    record = {"name": name, "data_source": "synthetic", "request": request,
                              "response": response, "replacement_output_hex": None if output is None else output.hex()}
                    guards.append(record)
                    try:
                        adapt_wa_mismatch_result(request, response, sequence=1, attempt=1,
                                                 node_id="local-eval-01", policy=policy)
                    except expected_error as error:
                        record.update(status="PASSED", error_type=type(error).__name__, error=str(error))
                    else:
                        raise AssertionError(f"适配层没有拒绝：{name}")
                print("v1 契约检查通过；5 项合成防误用检查通过")
                print("N3.1 WA 内容错误适配验证通过：WA/ANSWER_MISMATCH，0/2 个测试点通过")
                manifest.update(status="PASSED", adapted_cases=2, synthetic_guard_checks=len(guards))
                exit_code = 0
            except Exception as error:
                traceback.print_exc()
                manifest.update(status="FAILED", error=str(error))
    (evidence / "guards.json").write_text(json.dumps(guards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["exit_code"] = exit_code
    (evidence / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print((evidence / "verify.txt").read_text(encoding="utf-8"), end="")
    print("运行证据：", evidence.relative_to(root))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
