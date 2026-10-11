import subprocess

result = subprocess.run(
    [
        "g++",
        "-std=c++17",
        "examples/a_plus_b.cpp",
        "-o",
        "work/stage1/a_plus_b",
    ],
    capture_output=True,
    text=True,
)

print("编译退出码：", result.returncode)
print("编译器信息：", result.stderr)

if result.returncode != 0:
    raise SystemExit(1)

print("编译成功，可执行文件已生成。")

for case_id in ["01", "02"]:
    print("测试点：", case_id)

    with open(f"problems/a_plus_b/tests/{case_id}.in", encoding="utf-8") as input_file:
        input_text = input_file.read()

    with open(f"problems/a_plus_b/tests/{case_id}.out", encoding="utf-8") as answer_file:
        expected_output = answer_file.read()

    run_result = subprocess.run(
        ["work/stage1/a_plus_b"],
        input=input_text,
        capture_output=True,
        text=True,
    )

    print("程序退出码：", run_result.returncode)
    print("实际输出：", repr(run_result.stdout))
    print("预期输出：", repr(expected_output))
    print("输出一致：", run_result.stdout == expected_output)
    print("运行错误：", run_result.stderr)