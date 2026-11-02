# import subprocess

# result = subprocess.run(
#     [
#         "g++",
#         "-std=c++17",
#         "examples/a_plus_b.cpp",
#         "-o",
#         "work/stage1/a_plus_b",
#     ],
#     capture_output=True,
#     text=True,
# )

# print("编译退出码：", result.returncode)
# print("编译器信息：", result.stderr)

# if result.returncode != 0:
#     raise SystemExit(1)

# print("编译成功，可执行文件已生成。")
import subprocess
from pathlib import Path
from uuid import uuid4

from judge.compiler import compile_cpp
from judge.checker import check_output

run_id = f"demo_{uuid4().hex}"
build_dir = Path("work") / run_id / "T1"
print("本次编译目录：", build_dir)

compile_result = compile_cpp("examples/a_plus_b.cpp", build_dir)

print("编译退出码：", compile_result["exit_code"])
print("编译器信息：", compile_result["stderr"])

if not compile_result["success"]:
    raise SystemExit(1)

executable_path = compile_result["executable_path"]
print("编译成功，可执行文件已生成。")
print("本次可执行文件：", executable_path)

for case_id in ["01", "02"]:
    print("测试点：", case_id)

    with open(f"problems/a_plus_b/tests/{case_id}.in", encoding="utf-8") as input_file:
        input_text = input_file.read()

    with open(f"problems/a_plus_b/tests/{case_id}.out", encoding="utf-8") as answer_file:
        expected_output = answer_file.read()

    run_result = subprocess.run(
        # ["work/stage1/a_plus_b"],
        [executable_path],
        input=input_text,
        capture_output=True,
        text=True,
    )

    print("程序退出码：", run_result.returncode)
    print("实际输出：", repr(run_result.stdout))
    print("预期输出：", repr(expected_output))
    print("输出一致：", check_output(run_result.stdout, expected_output))
    print("运行错误：", run_result.stderr)