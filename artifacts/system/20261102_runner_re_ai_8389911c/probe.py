import json
import sys
from pathlib import Path
from judge.compiler import compile_cpp
from judge.runner import run_program

build_dir = Path(sys.argv[1])
result_path = Path(sys.argv[2])
compile_result = compile_cpp("examples/runtime_error.cpp", build_dir)
run_result = None
if compile_result["success"]:
    run_result = run_program(compile_result["executable_path"], "1 2\n")
expected = {
    "status": "FINISHED",
    "exit_code": 7,
    "stdout": "3\n",
    "stderr": "intentional nonzero exit\n",
    "error_message": "",
}
checks = {
    "compile_success": compile_result["success"],
    "compile_exit_zero": compile_result["exit_code"] == 0,
}
for field, value in expected.items():
    checks[field] = run_result is not None and run_result.get(field) == value
payload = {
    "executor": "AI",
    "input_text": "1 2\n",
    "compile_result": compile_result,
    "run_result": run_result,
    "expected_run_result": expected,
    "checks": checks,
    "matches_expectation": all(checks.values()),
}
result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
assert payload["matches_expectation"], "非零退出用例与预期不符"
