"""N1.4：验证输出正确但返回 7 的程序被判为 RE。"""

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
from tests.n1_wa import CaptureClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明", help="实际执行人，例如 lhy 或 AI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n1_re_{uuid4().hex[:12]}"
    work_dir = root / "work" / run_id
    evidence_dir = root / "artifacts" / "system" / run_id
    work_dir.mkdir(parents=True, exist_ok=False)
    evidence_dir.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/native/eval_server.cpp", "judge/native/Makefile",
        "judge/pipeline.py", "judge/problem.py", "tests/n1_re.py",
        "tests/n1_wa.py", "examples/runtime_error.cpp",
        "problems/a_plus_b/problem.json",
        "problems/a_plus_b/tests/01.in", "problems/a_plus_b/tests/01.out",
        "problems/a_plus_b/tests/02.in", "problems/a_plus_b/tests/02.out",
    ]
    manifest = {
        "run_id": run_id,
        "executed_by": args.actor,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "scope": "N1.4 主动返回 7 的 RE；未验证崩溃信号或资源超限",
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
                shutil.copyfile(root / "examples/runtime_error.cpp",
                                submissions_dir / "0004__a_plus_b.cpp")
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
                if not (submissions_dir / "0004__a_plus_b").is_file():
                    raise AssertionError("本次提交没有生成可执行文件")
                with (evidence_dir / "responses.json").open("x", encoding="utf-8") as response_file:
                    json.dump(captured.results, response_file, ensure_ascii=False, indent=2)
                    response_file.write("\n")

                with (evidence_dir / "result.tsv").open(encoding="utf-8", newline="") as result_file:
                    rows = list(csv.DictReader(result_file, delimiter="\t"))
                if len(rows) != 1 or rows[0]["verdict"] != "RE":
                    raise AssertionError(f"预期一份 RE 结果，实际为 {rows}")
                if (rows[0]["passed"], rows[0]["total"], rows[0]["cases"]) != ("0", "2", "01=RE;02=RE"):
                    raise AssertionError("RE 的计数或测试点汇总不符")

                results = {item["test_id"]: item for item in captured.results}
                if set(results) != {"01", "02"} or len(captured.results) != 2:
                    raise AssertionError("真实单点响应数量或编号不符")
                expected_error = b"intentional nonzero exit\n"
                for case in pipeline.problems["a_plus_b"].tests:
                    result = results[case.case_id]
                    if result["verdict"] != "RE" or result["exit_code"] != 7:
                        raise AssertionError(f"测试点 {case.case_id} 并非返回 7 的 RE：{result}")
                    if result["signal"] != 0 or result["wall_timeout"] or result["error"]:
                        raise AssertionError(f"测试点 {case.case_id} 出现其他异常：{result}")
                    actual_dir = submissions_dir / "0004__a_plus_b__result"
                    actual = (actual_dir / f"{case.case_id}.out").read_bytes()
                    expected = case.output_path.read_bytes()
                    stderr = (actual_dir / f"{case.case_id}.err").read_bytes()
                    if actual != expected:
                        raise AssertionError("本样例应当输出正确，再通过非零退出触发 RE")
                    if stderr != expected_error or result["stderr_tail"] != expected_error.decode():
                        raise AssertionError("运行错误提示未被正确保留")
                    print(f"测试点 {case.case_id}：RE，程序退出码 7，实际={actual!r}，预期={expected!r}，输出一致")
                    print("运行错误：", result["stderr_tail"].rstrip("\n"))
                    with (evidence_dir / f"{case.case_id}.actual.out").open("xb") as output_file:
                        output_file.write(actual)
                print("N1.4 运行错误验证通过：RE，0/2 个测试点通过")
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
