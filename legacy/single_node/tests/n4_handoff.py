"""Package the local acceptance sources and replay them outside the repository."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from uuid import uuid4

from tests.n32_services import require, write_json

ROOT = Path(__file__).resolve().parents[1]


def sources():
    files = {
        "README.md", "docs/ops/deployment.md", "docs/tasks/system/new_plan_1110.md",
        "docs/specs/stage1_agreement.md", "docs/project/AGENTS.md",
        "docs/project/distributed_implementation_plan.md", "docs/project/next_step.md",
        "judge/native/Makefile", "judge/native/eval-worker.example.json",
        "judge/problem.py", "judge/__init__.py", "tests/__init__.py",
        "tests/n32_services.py", "tests/n33_compile_batch.py", "tests/n34_eval_batch.py",
        "docs/project/second_report_plan.md",
        "tests/n4_handoff.py", "scripts/setup_n32.sh", "docs/tasks/application/handoff_n1_n4.md",
        "web/index.html", "web/style.css", "web/app.js",
    }
    for pattern in ("scripts/*.py", "judge/native/*.cpp", "judge/native/*.hpp",
                    "protocol/*.py", "protocol/*.hpp", "protocol/*.json", "protocol/golden/*.json",
                    "deploy/n32/*", "examples/*.cpp", "problems/a_plus_b/problem.json",
                    "problems/a_plus_b/tests/*.in", "problems/a_plus_b/tests/*.out",
                    "problems/lazy_gold/problem.json", "problems/lazy_gold/tests/*.in",
                    "problems/lazy_gold/tests/*.out"):
        files.update(str(path.relative_to(ROOT)) for path in ROOT.glob(pattern) if path.is_file())
    return sorted(files)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(work, evidence, actor, manifest):
    runtime = ROOT / "work/n32_runtime"
    require((runtime / "venv/bin/python").is_file(), "先运行 bash scripts/setup_n32.sh")
    paths = sources()
    manifest["source_sha256"] = {name: digest(ROOT / name) for name in paths}
    package = work / "algoos-local-handoff.tar.gz"
    with tarfile.open(package, "x:gz") as archive:
        for name in paths:
            require(not (ROOT / name).is_symlink(), f"源文件不应是符号链接：{name}")
            archive.add(ROOT / name, arcname=name, recursive=False)
    manifest["archive"] = str(package.relative_to(ROOT))
    manifest["archive_sha256"] = digest(package)
    print(f"[通过] 交接源码包：{manifest['archive']}（{len(paths)} 个文件）", flush=True)

    with TemporaryDirectory(prefix="algoos_handoff_", dir="/tmp") as temp:
        staged = Path(temp)
        with tarfile.open(package, "r:gz") as archive:
            archive.extractall(staged, filter="data")
        require(all(digest(staged / name) == sha for name, sha in manifest["source_sha256"].items()),
                "解包源码哈希不一致")
        require(not (staged / "work").exists(), "源码包不应包含本机依赖")
        (staged / "work").mkdir()
        # Reuse already prepared tools only; source, builds, data and evidence are fresh.
        (staged / "work/n32_runtime").symlink_to(runtime.resolve(), target_is_directory=True)
        with (evidence / "replay.txt").open("wb") as log:
            proc = subprocess.run([runtime / "venv/bin/python", "-I", "-c",
                                   "import runpy,sys; sys.path.insert(0,'.'); "
                                   "runpy.run_module('tests.n34_eval_batch',run_name='__main__')",
                                   "--recovery", "--actor", actor],
                                  cwd=staged, stdout=log, stderr=subprocess.STDOUT, timeout=180)
        runs = list((staged / "artifacts/system").glob("n36_recovery_*"))
        if len(runs) == 1:
            shutil.copytree(runs[0], evidence / "replay")
        require(proc.returncode == 0 and len(runs) == 1, "隔离目录复跑失败，见 replay.txt")
        replay = json.loads((evidence / "replay/manifest.json").read_text())
        require(replay["status"] == "PASSED", "复跑未通过")
        expected = [("COMPILED", 2), ("COMPILED", 2), ("CE", 0), ("SE", 0)]
        require([(c["status"], len(c["eval_tasks"])) for c in replay["cases"]] == expected,
                "编译和派发结果不符")
        require([item["status"] for item in replay["evaluations"]] == ["AC", "WA"], "评测结果不符")
        require(len(replay["redeliveries"]) == len(replay["duplicate_results"]) == 2, "恢复证据不全")
        # Ensure the unpacked handoff can load its own records without the original checkout.
        subprocess.run([runtime / "venv/bin/python", "-I", "-c",
                        "import sys; sys.path.insert(0,'.'); "
                        "from scripts.demo_web import load_records,latest_evidence; "
                        "from pathlib import Path; r=load_records(Path(sys.argv[1])); "
                        "assert len(r)==4; assert Path('web/index.html').is_file()",
                        str(runs[0] / "manifest.json")], cwd=staged, check=True, timeout=10)
        manifest["runtime_reused"] = True
        manifest["replay"] = "replay/manifest.json"

    finals = {item["file_id"]: item["status"] for item in replay["evaluations"]}
    with (evidence / "correctness.csv").open("w", encoding="utf-8-sig", newline="") as output:
        rows = csv.writer(output)
        rows.writerow(("fixture", "submission_id", "compile_status", "eval_tasks", "final_verdict"))
        for case in replay["cases"]:
            file_id = case["task"]["file_id"]
            rows.writerow((case["fixture"], file_id, case["status"], len(case["eval_tasks"]),
                           finals.get(file_id, case["status"])))
    print("[通过] 仓库外重新构建并复跑：AC、WA、CE、SE，含两节点重投与去重", flush=True)
    print("[通过] 已导出判定对照表 correctness.csv", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    run_id = "n4_handoff_" + uuid4().hex[:12]
    work, evidence = ROOT / "work" / run_id, ROOT / "artifacts/system" / run_id
    work.mkdir(parents=True)
    evidence.mkdir(parents=True)
    manifest = dict(executed_by=args.actor, scope="local handoff rehearsal; trusted fixtures")
    try:
        verify(work, evidence, args.actor, manifest)
        manifest.update(status="PASSED", exit_code=0)
        print("N4 本机交接预演通过")
    except Exception as error:
        manifest.update(status="FAILED", exit_code=1, error=str(error))
        print(f"N4 本机交接预演未通过：{error}")
    write_json(evidence / "manifest.json", manifest)
    print("运行证据：", evidence.relative_to(ROOT))
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
