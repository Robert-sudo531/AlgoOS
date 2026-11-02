import sys

from judge.runner import run_program

test_code = "import time\nprint('started', flush=True)\ntime.sleep(3)\n"

result = run_program(
    sys.executable,
    test_code,
    timeout_seconds=1,
)

print("运行状态：", result["status"])
print("程序退出码：", result["exit_code"])
print("已捕获输出：", repr(result["stdout"]))
print("运行错误：", repr(result["stderr"]))
print("运行说明：", result["error_message"])

assert result["status"] == "RUN_TIMEOUT"
assert result["exit_code"] is None
assert result["stdout"] == "started\n"

print("超时验证通过")