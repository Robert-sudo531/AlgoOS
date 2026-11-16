"""题目配置模型：problem.json 读取与派生值。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

COMMON_COMPARE_MODE = "common"
SPECIAL_COMPARE_MODE = "special"


def normalize_newlines(data: bytes) -> bytes:
    """CRLF/CR -> LF。"""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def trim_common(data: bytes) -> bytes:
    """common 比较口径：去每行末尾空格/制表符，再去文件末尾空行。"""
    lines = [line.rstrip(b" \t") for line in normalize_newlines(data).split(b"\n")]
    while lines and lines[-1] == b"":
        lines.pop()
    return b"\n".join(lines)


@dataclass(frozen=True)
class TestCase:
    case_id: str
    input_path: Path
    output_path: Path
    out_bytes: int


@dataclass(frozen=True)
class Problem:
    problem_id: str
    title: str
    time_limit_ms: int
    memory_limit_mib: int
    compare_mode: str
    tests: tuple[TestCase, ...]
    config_path: Path


def load_problem(config_path: Path | str) -> Problem:
    """读取一份 problem.json，并计算每个测试点的 trim 后输出上限。"""
    path = Path(config_path).resolve()
    with path.open(encoding="utf-8") as config_file:
        data = json.load(config_file)

    base = path.parent
    tests: list[TestCase] = []
    for raw_test in data["tests"]:
        output_path = base / raw_test["output"]
        output_bytes = output_path.read_bytes()
        tests.append(
            TestCase(
                case_id=str(raw_test["case_id"]),
                input_path=base / raw_test["input"],
                output_path=output_path,
                out_bytes=len(trim_common(output_bytes)),
            )
        )

    return Problem(
        problem_id=str(data["problem_id"]),
        title=str(data["title"]),
        time_limit_ms=int(data["time_limit_ms"]),
        memory_limit_mib=int(data["memory_limit_mib"]),
        compare_mode=str(data["compare_mode"]),
        tests=tuple(tests),
        config_path=path,
    )


def load_all_problems(problems_dir: Path | str) -> dict[str, Problem]:
    """扫描 problems/*/problem.json，建立 problem_id -> Problem 注册表。"""
    root = Path(problems_dir)
    problems: dict[str, Problem] = {}
    for config_path in sorted(root.glob("*/problem.json")):
        problem = load_problem(config_path)
        problems[problem.problem_id] = problem
    return problems
