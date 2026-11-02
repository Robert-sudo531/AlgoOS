import json
import sys
from pathlib import Path
from judge.problem import load_problem

config_path = Path(sys.argv[1])
raw = json.loads(config_path.read_text(encoding="utf-8"))
score = raw["tests"][1]["score"]
print("测试来源：AI", flush=True)
print("配置：", config_path, flush=True)
print("第二组原始分值：", repr(score), flush=True)
print("第二组分值类型：", type(score).__name__, flush=True)
problem = load_problem(config_path)
print("题目编号：", problem["problem_id"])
for test in problem["tests"]:
    print("测试点：", test["case_id"], "分值：", test["score"])
print("配置总分：", sum(test["score"] for test in problem["tests"]))
