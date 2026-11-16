"""N1.3：用真实编译流程验证语法错误返回 CE，且不调用测试点评测。"""

import argparse
import contextlib
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import traceback
from uuid import uuid4

from judge.pipeline import Pipeline


class CaptureCompiler:
    """调用原编译器，保留真实编译结果和完整诊断。"""

    def __init__(self, compiler):
        self.compiler = compiler
        self.calls = 0
        self.result = None

    def compile(self, spec):
        self.calls += 1
        self.result = self.compiler.compile(spec)
        return self.result


class RejectEvaluation:
    """CE 后一旦尝试评测，本次验证就失败。"""

    def __init__(self):
        self.called = False

    def evaluate(self, requests, workers):
        self.called = True
        raise AssertionError("编译失败后不应调用测试点评测")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", default="未注明", help="实际执行人，例如 lhy 或 AI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run_id = f"n1_ce_{uuid4().hex[:12]}"
    work_dir = root / "work" / run_id
    evidence_dir = root / "artifacts" / "system" / run_id
    work_dir.mkdir(parents=True, exist_ok=False)
    evidence_dir.mkdir(parents=True, exist_ok=False)
    inputs = [
        "judge/pipeline.py", "judge/problem.py", "tests/n1_ce.py",
        "examples/compile_error.cpp", "problems/a_plus_b/problem.json",
        "problems/a_plus_b/tests/01.in", "problems/a_plus_b/tests/01.out",
        "problems/a_plus_b/tests/02.in", "problems/a_plus_b/tests/02.out",
    ]
    manifest = {
        "run_id": run_id,
        "executed_by": args.actor,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "scope": "N1.3 真实语法编译错误及 CE 阻断；不启动原生评测服务",
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
                submissions_dir = work_dir / "submissions"
                submissions_dir.mkdir()
                source_path = submissions_dir / "0003__a_plus_b.cpp"
                shutil.copyfile(root / "examples/compile_error.cpp", source_path)
                pipeline = Pipeline(
                    problems_dir=root / "problems",
                    submissions_dir=submissions_dir,
                    result_dir=evidence_dir,
                    server_bin=work_dir / "unused_eval_server",
                    workers=1,
                )
                compiler = CaptureCompiler(pipeline.compiler)
                guard = RejectEvaluation()
                pipeline.compiler = compiler
                pipeline.client = guard
                # 只执行真实编译与提交处理；CE 路径无需启动 C++ 服务。
                pipeline.process_existing()

                compiled = compiler.result
                if compiler.calls != 1 or compiled is None:
                    raise AssertionError("预期恰好编译一次")
                with (evidence_dir / "compile.txt").open("x", encoding="utf-8") as compile_log:
                    compile_log.write(compiled.log)
                if compiled.ok or compiled.bin_path is not None:
                    raise AssertionError("错误源码不应编译成功")
                if source_path.name not in compiled.log:
                    raise AssertionError("预期具体源码的编译诊断，而非编译超时")
                if guard.called:
                    raise AssertionError("编译失败后仍调用了测试点评测")
                if source_path.with_suffix("").exists():
                    raise AssertionError("编译失败后不应存在可执行产物")
                if (submissions_dir / "0003__a_plus_b__result").exists():
                    raise AssertionError("编译失败后不应创建测试点输出目录")
                with (evidence_dir / "result.tsv").open(encoding="utf-8", newline="") as result_file:
                    rows = list(csv.DictReader(result_file, delimiter="\t"))
                if len(rows) != 1 or rows[0]["verdict"] != "CE":
                    raise AssertionError(f"预期一份 CE 结果，实际为 {rows}")
                if (rows[0]["passed"], rows[0]["total"], rows[0]["cases"]) != ("0", "2", ""):
                    raise AssertionError("CE 的计数或空测试点结果不符")
                if not rows[0]["message"].strip() or rows[0]["message"] == "-":
                    raise AssertionError("CE 结果缺少编译诊断摘要")

                manifest["compile_calls"] = compiler.calls
                manifest["compile_success"] = compiled.ok
                manifest["evaluation_called"] = guard.called
                print("编译成功：", compiled.ok)
                print("编译诊断：\n" + compiled.log, end="")
                print("是否调用测试点评测：", guard.called)
                print("N1.3 编译错误验证通过：CE，未运行测试点")
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
