"""Build and verify the standalone local compiler with repository fixtures."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = "n33_compile_local_" + uuid4().hex[:12]
    work, evidence = root / "work" / run, root / "artifacts/system" / run
    work.mkdir(parents=True)
    evidence.mkdir(parents=True)
    sources = ["judge/native/Makefile", "judge/native/compile_local.cpp",
               "examples/a_plus_b.cpp", "examples/compile_error.cpp", "tests/n33_compile_local.py"]
    record = {"executed_by": args.actor, "cases": [],
              "source_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in sources}}
    try:
        command = ["make", "-C", str(root / "judge/native"), f"COMPILE_BUILD_DIR={work}", "compile-local"]
        with (evidence / "build.txt").open("wb") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        for source, expected, code in (("a_plus_b", "COMPILED", 0), ("compile_error", "CE", 2)):
            output = work / source
            command = [str(work / "compile-local"), str(root / "examples" / (source + ".cpp")), str(output)]
            proc = subprocess.run(command, capture_output=True, timeout=30)
            diagnostics = evidence / (source + ".stderr")
            diagnostics.write_bytes(proc.stderr)
            binary = output / "program"
            record["cases"].append({"command": command, "exit_code": proc.returncode,
                                    "stdout": proc.stdout.decode(), "binary_exists": binary.is_file(),
                                    "diagnostics": diagnostics.name})
            if (proc.returncode, proc.stdout.strip(), binary.is_file()) != (code, expected.encode(), code == 0):
                raise RuntimeError(f"{source}: unexpected compile result")
            if code == 0:
                result = subprocess.run([binary], input=b"1 2\n", capture_output=True, check=True, timeout=5)
                if result.stdout != b"3\n":
                    raise RuntimeError("compiled program returned unexpected output")
            elif not proc.stderr:
                raise RuntimeError("CE missing compiler diagnostics")
            print(f"[通过] {source}: {expected}")
        record.update(status="PASSED", exit_code=0)
        print("N3.3 独立编译入口验证通过")
    except Exception as error:
        record.update(status="FAILED", exit_code=1, error=str(error))
        print(f"验证失败：{error}")
    (evidence / "manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print("运行证据：", evidence.relative_to(root))
    return record["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
