from pathlib import Path
from judge.problem import load_problem

config_path = Path("work/stage2_fixture/a_plus_b/problem.json")
problem = load_problem(config_path)

print("题目编号：", problem["problem_id"])

total_score = 0

for test in problem["tests"]:
    input_path = config_path.parent / test["input"]
    output_path = config_path.parent / test["output"]

    print("测试点：", test["case_id"])
    print("输入文件：", input_path)
    print("输出文件：", output_path)
    print("分值：", test["score"])

    total_score += test["score"]

print("配置总分：", total_score)