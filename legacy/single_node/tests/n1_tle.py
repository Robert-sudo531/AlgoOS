"""N1.5：验证墙钟超时终止与 TLE；不单独验证 CPU 超限。"""

import argparse
import contextlib
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import traceback
from uuid import uuid4

from judge.pipeline import Pipeline
from tests.n1_wa import CaptureClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明", help="实际执行人，例如 lhy 或 AI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n1_tle_{uuid4().hex[:12]}"
    work_dir = root / "work" / run_id
    evidence_dir = root / "artifacts" / "system" / run_id
    work_dir.mkdir(parents=True, exist_ok=False)
    evidence_dir.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/native/eval_server.cpp", "judge/native/Makefile",
        "judge/pipeline.py", "judge/problem.py", "tests/n1_tle.py",
        "tests/n1_wa.py", "examples/time_limit.cpp",
        "problems/a_plus_b/problem.json",
        "problems/a_plus_b/tests/01.in", "problems/a_plus_b/tests/01.out",
        "problems/a_plus_b/tests/02.in", "problems/a_plus_b/tests/02.out",
    ]
    manifest = {
        "run_id": run_id,
        "executed_by": args.actor,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "scope": "N1.5 休眠触发墙钟超时；未单独验证 CPU 超限或子孙进程清理",
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
                shutil.copyfile(root / "examples/time_limit.cpp",
                                submissions_dir / "0005__a_plus_b.cpp")
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
                if not (submissions_dir / "0005__a_plus_b").is_file():
                    raise AssertionError("本次提交没有生成可执行文件")
                with (evidence_dir / "responses.json").open("x", encoding="utf-8") as response_file:
                    json.dump(captured.results, response_file, ensure_ascii=False, indent=2)
                    response_file.write("\n")

                with (evidence_dir / "result.tsv").open(encoding="utf-8", newline="") as result_file:
                    rows = list(csv.DictReader(result_file, delimiter="\t"))
                if len(rows) != 1 or rows[0]["verdict"] != "TLE":
                    raise AssertionError(f"预期一份 TLE 结果，实际为 {rows}")
                if (rows[0]["passed"], rows[0]["total"], rows[0]["cases"]) != ("0", "2", "01=TLE;02=TLE"):
                    raise AssertionError("TLE 的计数或测试点汇总不符")

                requests = {item["test_id"]: item for item in captured.requests}
                results = {item["test_id"]: item for item in captured.results}
                if set(results) != {"01", "02"} or len(captured.results) != 2:
                    raise AssertionError("真实单点响应数量或编号不符")
                for case in pipeline.problems["a_plus_b"].tests:
                    result = results[case.case_id]
                    if requests[case.case_id]["limits"]["wall_ms"] != 1000:
                        raise AssertionError("本样例预期使用原题包的 1000 毫秒墙钟限制")
                    if result["verdict"] != "TLE" or not result["wall_timeout"]:
                        raise AssertionError(f"测试点 {case.case_id} 未触发墙钟 TLE：{result}")
                    if result["signal"] != signal.SIGKILL or result["exit_code"] != -1:
                        raise AssertionError(f"测试点 {case.case_id} 未被预期信号终止：{result}")
                    if result["error"] or result["stderr_tail"]:
                        raise AssertionError(f"测试点 {case.case_id} 出现其他错误：{result}")
                    actual_dir = submissions_dir / "0005__a_plus_b__result"
                    actual = (actual_dir / f"{case.case_id}.out").read_bytes()
                    expected = case.output_path.read_bytes()
                    if actual != expected:
                        raise AssertionError("超时前输出不符，或程序运行到了休眠后的 finished 标记")
                    if (actual_dir / f"{case.case_id}.err").read_bytes():
                        raise AssertionError("本次超时样例不应产生运行错误文本")
                    print(f"测试点 {case.case_id}：TLE，墙钟超时=True，终止信号={result['signal']}，超时前输出={actual!r}")
                    with (evidence_dir / f"{case.case_id}.actual.out").open("xb") as output_file:
                        output_file.write(actual)
                print("N1.5 墙钟超时验证通过：TLE，0/2 个测试点通过")
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
