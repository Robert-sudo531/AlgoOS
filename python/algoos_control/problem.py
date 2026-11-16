"""Problem package import: config validation, bundle hashing, checker build."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from algoos_protocol import hash_bytes

from .storage import ObjectStore, make_zip


class ProblemError(ValueError):
    pass


@dataclass(frozen=True)
class TestCase:
    case_id: str
    input: str
    output: str


@dataclass(frozen=True)
class Problem:
    problem_id: str
    title: str
    time_limit_ms: int
    memory_limit_mib: int
    compare_mode: str
    judge_mode: str
    tests: tuple[TestCase, ...]
    config_path: Path
    checker_source: Path | None = None

    @property
    def wall_limit_ms(self) -> int:
        return self.time_limit_ms + 1000

    @property
    def memory_bytes(self) -> int:
        return self.memory_limit_mib << 20

    def test(self, case_id: str) -> TestCase:
        for case in self.tests:
            if case.case_id == case_id:
                return case
        raise ProblemError(f"unknown test id: {case_id}")

    def derived_out_bytes(self, case: TestCase) -> int:
        from .text import normalize_common

        answer = (self.config_path.parent / case.output).read_bytes()
        return len(normalize_common(answer))


def load_problem(path: Path | str) -> Problem:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    data = json.loads(config_path.read_text(encoding="utf-8"))
    required = {"problem_id", "title", "time_limit_ms", "memory_limit_mib", "compare_mode", "tests"}
    missing = required - set(data)
    if missing:
        raise ProblemError(f"{config_path}: missing fields {sorted(missing)}")
    compare_mode = data["compare_mode"]
    if compare_mode not in {"common", "special"}:
        raise ProblemError("compare_mode must be common or special")
    judge_mode = data.get("judge_mode", "normal")
    if judge_mode not in {"normal", "special"}:
        raise ProblemError("judge_mode must be normal or special")
    if judge_mode == "special" and compare_mode != "special":
        raise ProblemError("special judge requires compare_mode=special")
    tests = []
    for entry in data["tests"]:
        for key in ("case_id", "input", "output"):
            if key not in entry or not entry[key]:
                raise ProblemError(f"test entry missing {key}")
        case = TestCase(str(entry["case_id"]), entry["input"], entry["output"])
        for relative in (case.input, case.output):
            if not (config_path.parent / relative).is_file():
                raise ProblemError(f"missing test file: {relative}")
        tests.append(case)
    if not tests:
        raise ProblemError("problem has no tests")
    if len({case.case_id for case in tests}) != len(tests):
        raise ProblemError("duplicate case_id")
    checker = None
    if judge_mode == "special":
        checker_path = data.get("checker")
        if not checker_path:
            raise ProblemError("special problem requires a checker source")
        checker = config_path.parent / checker_path
        if not checker.is_file():
            raise ProblemError(f"missing checker source: {checker_path}")
    return Problem(
        problem_id=data["problem_id"], title=data["title"],
        time_limit_ms=int(data["time_limit_ms"]),
        memory_limit_mib=int(data["memory_limit_mib"]),
        compare_mode=compare_mode, judge_mode=judge_mode, tests=tuple(tests),
        config_path=config_path, checker_source=checker,
    )


@dataclass(frozen=True)
class ImportedProblem:
    problem: Problem
    bundle_hash: str
    checker_hash: str | None
    bundle_bytes: bytes
    case_hashes: dict[str, tuple[str, str]]  # case_id -> (input_hash, answer_hash)



def build_bundle(problem: Problem) -> tuple[bytes, str]:
    base = problem.config_path.parent
    entries = {"problem.json": problem.config_path.read_bytes()}
    for case in problem.tests:
        entries[f"tests/{case.case_id}.in"] = (base / case.input).read_bytes()
        entries[f"tests/{case.case_id}.out"] = (base / case.output).read_bytes()
    data = make_zip(entries)
    return data, hash_bytes(data)


def build_checker(problem: Problem) -> bytes | None:
    if problem.checker_source is None:
        return None
    with TemporaryDirectory(prefix="algoos-checker-") as directory:
        binary = Path(directory) / "checker"
        result = subprocess.run(
            ["g++", "-std=c++17", "-O2", "-pipe", str(problem.checker_source), "-o", str(binary)],
            capture_output=True, timeout=120)
        if result.returncode != 0:
            raise ProblemError(
                f"checker build failed: {result.stderr.decode('utf-8', 'replace')[-2000:]}")
        return binary.read_bytes()


def import_problem(problem: Problem, store: ObjectStore) -> ImportedProblem:
    bundle, bundle_hash = build_bundle(problem)
    if not store.exists(store.bundle_key(bundle_hash)):
        store.put(store.bundle_key(bundle_hash), bundle)
    base = problem.config_path.parent
    case_hashes: dict[str, tuple[str, str]] = {}
    for case in problem.tests:
        input_bytes = (base / case.input).read_bytes()
        answer_bytes = (base / case.output).read_bytes()
        input_hash = hash_bytes(input_bytes)
        answer_hash = hash_bytes(answer_bytes)
        store.put(store.input_key(input_hash), input_bytes)
        store.put(store.answer_key(answer_hash), answer_bytes)
        case_hashes[case.case_id] = (input_hash, answer_hash)
    checker_hash = None
    if problem.judge_mode == "special":
        checker = build_checker(problem)
        assert checker is not None
        checker_hash = hash_bytes(checker)
        if not store.exists(store.checker_key(checker_hash)):
            store.put(store.checker_key(checker_hash), checker)
    return ImportedProblem(problem=problem, bundle_hash=bundle_hash,
                           checker_hash=checker_hash, bundle_bytes=bundle,
                           case_hashes=case_hashes)
