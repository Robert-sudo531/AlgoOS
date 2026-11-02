from pathlib import Path
from uuid import uuid4

from judge.compiler import compile_cpp

run_id = f"compile_{uuid4().hex}"
build_dir = Path("work") / run_id / "T1"

result = compile_cpp("examples/a_plus_b.cpp", build_dir)

print("编译退出码：", result["exit_code"])
print("编译成功：", result["success"])
print("可执行文件：", result["executable_path"])
print("编译器信息：", result["stderr"])

if not result["success"]:
    raise SystemExit(1)

print("产物存在：", Path(result["executable_path"]).is_file())