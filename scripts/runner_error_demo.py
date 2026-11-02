from pathlib import Path
from uuid import uuid4

from judge.runner import run_program

missing_path = Path("work") / f"missing_{uuid4().hex}" / "program"
assert not missing_path.exists()

result = run_program(missing_path, "")

print("缺失路径：", missing_path)
print("运行状态：", result["status"])
print("程序退出码：", result["exit_code"])
print("标准输出：", repr(result["stdout"]))
print("标准错误：", repr(result["stderr"]))
print("错误原因：", result["error_message"])

assert result["status"] == "SYSTEM_ERROR"
assert result["exit_code"] is None
assert result["stdout"] == ""
assert result["stderr"] == ""
assert str(missing_path) in result["error_message"]

print("环境错误验证通过")