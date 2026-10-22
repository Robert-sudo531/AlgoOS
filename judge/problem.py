import json
from pathlib import Path


def load_problem(config_path):
    config_path = Path(config_path)

    with config_path.open(encoding="utf-8") as config_file:
        problem = json.load(config_file)

    seen_case_ids = set()
    for test in problem["tests"]:
        case_id = test.get("case_id")

        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("测试点编号必须是非空字符串")

        if case_id in seen_case_ids:
            raise ValueError(f"测试点编号重复：{case_id}")

        seen_case_ids.add(case_id)
        
        input_path = config_path.parent / test["input"]
        output_path = config_path.parent / test["output"]

        if not input_path.is_file():
            raise FileNotFoundError(f"输入文件不存在：{input_path}")

        if not output_path.is_file():
            raise FileNotFoundError(f"标准答案文件不存在：{output_path}")

    return problem