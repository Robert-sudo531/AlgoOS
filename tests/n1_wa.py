"""N1.2：验证正常退出的错误答案被判为 WA，保留真实单点响应。"""

import argparse
import contextlib
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


class CaptureClient:
    """透传真实评测请求，只额外保存响应，不改变判定。"""

    def __init__(self, client):
        self.client = client
        self.requests = []
        self.results = []

    def evaluate(self, requests, workers):
        self.requests = requests
        self.results = self.client.evaluate(requests, workers=workers)
        return self.results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明", help="实际执行人，例如 lhy 或 AI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n1_wa_{uuid4().hex[:12]}"
    work_dir = root / "work" / run_id
    evidence_dir = root / "artifacts" / "system" / run_id
    work_dir.mkdir(parents=True, exist_ok=False)
    evidence_dir.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/native/eval_server.cpp", "judge/native/Makefile",
        "judge/pipeline.py", "judge/problem.py", "tests/n1_wa.py",
        "examples/wrong_answer.cpp", "problems/a_plus_b/problem.json",
        "problems/a_plus_b/tests/01.in", "problems/a_plus_b/tests/01.out",
        "problems/a_plus_b/tests/02.in", "problems/a_plus_b/tests/02.out",
    ]
    manifest = {
        "run_id": run_id,
        "executed_by": args.actor,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "scope": "N1.2 正常退出、输出不超限的错误答案；只验证 WA",
        "status": "RUNNING",
        "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in inputs
        },
    }
    exit_code = 1
    with (evidence_dir / "verify.txt").open("x", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                command = ["make", "-C", str(root / "judge/native"),
                           f"BUILD_DIR={work_dir / 'native'}"]
                manifest["build_command"] = command
                with (evidence_dir / "build.txt").open("x", encoding="utf-8") as build_log:
                    build = subprocess.run(command, stdout=build_log,
                                           stderr=subprocess.STDOUT, timeout=120)
                manifest["build_exit_code"] = build.returncode
                if build.returncode != 0:
                    raise RuntimeError("服务构建失败，请查看 build.txt")

                submissions_dir = work_dir / "submissions"
                submissions_dir.mkdir()
                shutil.copyfile(root / "examples/wrong_answer.cpp",
                                submissions_dir / "0002__a_plus_b.cpp")
                pipeline = Pipeline(
                    problems_dir=root / "problems",
                    submissions_dir=submissions_dir,
                    result_dir=evidence_dir,
                    server_bin=work_dir / "native" / "eval_server",
                    workers=1,
                )
                try:
                    pipeline.start_server()
                    captured = CaptureClient(pipeline.client)
                    pipeline.client = captured
                    pipeline.process_existing()
                finally:
                    pipeline.stop_server()
                if not (submissions_dir / "0002__a_plus_b").is_file():
                    raise AssertionError("本次提交没有生成可执行文件")
                with (evidence_dir / "responses.json").open("x", encoding="utf-8") as response_file:
                    json.dump(captured.results, response_file, ensure_ascii=False, indent=2)
                    response_file.write("\n")

                with (evidence_dir / "result.tsv").open(encoding="utf-8", newline="") as result_file:
                    rows = list(csv.DictReader(result_file, delimiter="\t"))
                if len(rows) != 1 or rows[0]["verdict"] != "WA":
                    raise AssertionError(f"预期一份 WA 结果，实际为 {rows}")
                if rows[0]["passed"] != "0" or rows[0]["total"] != "2":
                    raise AssertionError("预期两个测试点均不通过")

                requests = {item["test_id"]: item for item in captured.requests}
                results = {item["test_id"]: item for item in captured.results}
                expected_wrong = {"01": b"1\n", "02": b"11\n"}
                if set(results) != set(expected_wrong) or len(captured.results) != 2:
                    raise AssertionError("真实单点响应数量或编号不符")
                for case in pipeline.problems["a_plus_b"].tests:
                    result = results[case.case_id]
                    if result["verdict"] != "WA" or result["exit_code"] != 0 or result["signal"] != 0 or result["wall_timeout"]:
                        raise AssertionError(f"测试点 {case.case_id} 并非正常退出的 WA：{result}")
                    if result["error"]:
                        raise AssertionError(f"测试点 {case.case_id} 存在系统错误：{result['error']}")
                    actual_path = submissions_dir / "0002__a_plus_b__result" / f"{case.case_id}.out"
                    actual = actual_path.read_bytes()
                    expected = case.output_path.read_bytes()
                    if actual != expected_wrong[case.case_id] or trim_common(actual) == trim_common(expected):
                        raise AssertionError(f"测试点 {case.case_id} 没有产生预期的错误答案")
                    limit = requests[case.case_id]["limits"]["out_bytes"]
                    if len(trim_common(actual)) > limit or result["out_bytes"] > limit:
                        raise AssertionError("本测试应验证内容错误，而非输出超限")
                    print(f"测试点 {case.case_id}：WA，程序退出码 0，实际={actual!r}，预期={expected!r}，输出未超限")
                    with (evidence_dir / f"{case.case_id}.actual.out").open("xb") as output_file:
                        output_file.write(actual)
                print("N1.2 错误答案验证通过：WA，0/2 个测试点通过")
                manifest["status"] = "PASSED"
                exit_code = 0
            except Exception as error:
                traceback.print_exc()
                manifest["status"] = "FAILED"
                manifest["error"] = str(error)

    manifest["exit_code"] = exit_code
    with (evidence_dir / "manifest.json").open("x", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, ensure_ascii=False, indent=2)
        manifest_file.write("\n")
    print((evidence_dir / "verify.txt").read_text(encoding="utf-8"), end="")
    print("运行证据：", evidence_dir.relative_to(root))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
