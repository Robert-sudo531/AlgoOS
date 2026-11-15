"""N1.1：构建现有服务，验证正确 A+B，并保留独立运行记录。"""

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明", help="实际执行人，例如 lhy 或 AI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n1_ac_{uuid4().hex[:12]}"
    work_dir = root / "work" / run_id
    evidence_dir = root / "artifacts" / "system" / run_id
    work_dir.mkdir(parents=True, exist_ok=False)
    evidence_dir.mkdir(parents=True, exist_ok=False)

    inputs = [
        "judge/native/eval_server.cpp", "judge/native/Makefile",
        "judge/pipeline.py", "judge/problem.py", "tests/n1_ac.py",
        "examples/a_plus_b.cpp", "problems/a_plus_b/problem.json",
        "problems/a_plus_b/tests/01.in", "problems/a_plus_b/tests/01.out",
        "problems/a_plus_b/tests/02.in", "problems/a_plus_b/tests/02.out",
    ]
    manifest = {
        "run_id": run_id,
        "executed_by": args.actor,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "scope": "N1.1 正确 A+B；未验证错误提交、CPU/MLE 隔离或分布式能力",
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
                shutil.copyfile(root / "examples/a_plus_b.cpp",
                                submissions_dir / "0001__a_plus_b.cpp")
                pipeline = Pipeline(
                    problems_dir=root / "problems",
                    submissions_dir=submissions_dir,
                    result_dir=evidence_dir,
                    server_bin=work_dir / "native" / "eval_server",
                    workers=1,
                )
                try:
                    pipeline.start_server()
                    pipeline.process_existing()
                finally:
                    pipeline.stop_server()

                with (evidence_dir / "result.tsv").open(encoding="utf-8", newline="") as result_file:
                    rows = list(csv.DictReader(result_file, delimiter="\t"))
                if len(rows) != 1 or rows[0]["verdict"] != "AC":
                    raise AssertionError(f"预期一份 AC 结果，实际为 {rows}")
                problem = pipeline.problems["a_plus_b"]
                if int(rows[0]["passed"]) != len(problem.tests) or int(rows[0]["total"]) != len(problem.tests):
                    raise AssertionError("测试点通过数或总数不符")
                for case in problem.tests:
                    actual_path = submissions_dir / "0001__a_plus_b__result" / f"{case.case_id}.out"
                    actual = actual_path.read_bytes()
                    expected = case.output_path.read_bytes()
                    print(f"测试点 {case.case_id}：实际={actual!r}，预期={expected!r}")
                    if actual != expected:
                        raise AssertionError(f"测试点 {case.case_id} 的实际输出不符")
                    with (evidence_dir / f"{case.case_id}.actual.out").open("xb") as output_file:
                        output_file.write(actual)
                print(f"N1.1 正常提交验证通过：AC {len(problem.tests)}/{len(problem.tests)}")
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
