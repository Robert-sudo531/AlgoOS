"""评测流水线：编译 submissions、调用 C++ 服务端、聚合、写 result.tsv。"""

from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import socket
import struct
import subprocess
import time
from typing import Any

from judge.problem import Problem, TestCase, load_all_problems


@dataclass(frozen=True)
class Limits:
    cpu_ms: int
    mem_bytes: int
    out_bytes: int
    wall_ms: int | None = None

    def __post_init__(self) -> None:
        if self.wall_ms is None:
            object.__setattr__(self, "wall_ms", self.cpu_ms)

    def to_json(self) -> dict[str, int]:
        return {
            "cpu_ms": self.cpu_ms,
            "mem_bytes": self.mem_bytes,
            "out_bytes": self.out_bytes,
            "wall_ms": int(self.wall_ms),
        }


@dataclass(frozen=True)
class SubmissionSpec:
    submission_id: str
    problem_id: str
    source_path: Path
    stem: str
    bin_path: Path
    result_dir: Path


@dataclass(frozen=True)
class CompileResult:
    ok: bool
    log: str
    bin_path: Path | None


def parse_submission_filename(source_path: Path) -> tuple[str, str]:
    """`001__lazy_gold.cpp` -> ("001", "lazy_gold")。"""
    submission_id, problem_id = source_path.stem.split("__", 1)
    return submission_id, problem_id


def make_submission_spec(source_path: Path) -> SubmissionSpec:
    submission_id, problem_id = parse_submission_filename(source_path)
    stem = source_path.stem
    return SubmissionSpec(
        submission_id=submission_id,
        problem_id=problem_id,
        source_path=source_path,
        stem=stem,
        bin_path=source_path.with_suffix(""),
        result_dir=source_path.parent / f"{stem}__result",
    )


class CompileScheduler:
    def __init__(self, gxx: str = "g++") -> None:
        self._gxx = gxx

    def compile(self, spec: SubmissionSpec) -> CompileResult:
        command = [
            self._gxx,
            "-std=c++17",
            "-O2",
            "-pipe",
            "-o",
            str(spec.bin_path),
            str(spec.source_path),
        ]
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=30, check=False
            )
        except subprocess.TimeoutExpired:
            return CompileResult(False, "编译超时（30 秒）", None)

        log = (proc.stdout or "") + (proc.stderr or "")
        return CompileResult(
            ok=proc.returncode == 0,
            log=log,
            bin_path=spec.bin_path if proc.returncode == 0 else None,
        )


class EvalClient:
    """长度前缀 + JSON 的 Socket 客户端。"""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    def evaluate(
        self, requests: list[dict[str, Any]], workers: int = 2
    ) -> list[dict[str, Any]]:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self._evaluate_one, requests))

    def _evaluate_one(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        with socket.create_connection((self._host, self._port), timeout=10) as sock:
            sock.settimeout(10)
            sock.sendall(struct.pack(">I", len(payload)) + payload)
            length = self._recv_exact(sock, 4)
            size = struct.unpack(">I", length)[0]
            data = self._recv_exact(sock, size)
        return json.loads(data.decode("utf-8", errors="replace"))

    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise ConnectionError("服务端提前关闭连接")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class ResultAggregator:
    def aggregate(
        self, cases: tuple[TestCase, ...], results: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        by_id = {str(result["test_id"]): result for result in results}
        ordered = [by_id[case.case_id] for case in cases]
        verdicts = [str(result["verdict"]) for result in ordered]
        if all(verdict == "AC" for verdict in verdicts):
            return "AC", ordered
        return next(verdict for verdict in verdicts if verdict != "AC"), ordered


RESULT_FIELDS = [
    "timestamp",
    "source_file",
    "problem_id",
    "submission_id",
    "verdict",
    "passed",
    "total",
    "cases",
    "elapsed_ms",
    "message",
]


class ResultWriter:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists() or self._path.stat().st_size == 0:
            with self._path.open("w", encoding="utf-8", newline="") as result_file:
                writer = csv.DictWriter(
                    result_file,
                    fieldnames=RESULT_FIELDS,
                    delimiter="\t",
                    lineterminator="\n",
                )
                writer.writeheader()

    def append(self, row: dict[str, Any]) -> None:
        with self._path.open("a", encoding="utf-8", newline="") as result_file:
            writer = csv.DictWriter(
                result_file,
                fieldnames=RESULT_FIELDS,
                delimiter="\t",
                lineterminator="\n",
                extrasaction="ignore",
            )
            writer.writerow(row)
            result_file.flush()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Pipeline:
    def __init__(
        self,
        problems_dir: Path,
        submissions_dir: Path,
        result_dir: Path,
        server_bin: Path,
        workers: int = 4,
        port: int = 0,
        poll_interval: float = 0.5,
    ) -> None:
        self.problems = load_all_problems(problems_dir)
        self.submissions_dir = submissions_dir
        self.result_dir = result_dir
        self.submissions_dir.mkdir(parents=True, exist_ok=True)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.result_writer = ResultWriter(result_dir / "result.tsv")
        self.compiler = CompileScheduler()
        self.aggregator = ResultAggregator()
        self.server_bin = server_bin
        self.workers = workers
        self.port = port
        self.poll_interval = poll_interval
        self.server: subprocess.Popen[str] | None = None
        self.client: EvalClient | None = None
        self.seen: set[Path] = set()

    def start_server(self) -> None:
        port = self.port or find_free_port()
        self.server = subprocess.Popen(
            [str(self.server_bin), "--port", str(port), "--workers", str(self.workers)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            if self.server.poll() is not None:
                raise RuntimeError("C++ 评测服务端提前退出")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    self.port = port
                    self.client = EvalClient("127.0.0.1", port)
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("C++ 评测服务端未在 5 秒内监听")

    def stop_server(self) -> None:
        if self.server is None:
            return
        self.server.terminate()
        try:
            self.server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait()
        if self.server.stdout is not None:
            self.server.stdout.close()
        if self.server.stderr is not None:
            self.server.stderr.close()

    def process_existing(self) -> None:
        for source_path in sorted(self.submissions_dir.glob("*.cpp")):
            self.seen.add(source_path)
            self.process_file(source_path)

    def poll_once(self) -> None:
        for source_path in sorted(self.submissions_dir.glob("*.cpp")):
            if source_path in self.seen:
                continue
            self.seen.add(source_path)
            self.process_file(source_path)

    def run_forever(self) -> None:
        self.start_server()
        print("[test_pipeline] problems=" + ",".join(sorted(self.problems)))
        print(
            f"[test_pipeline] server=127.0.0.1:{self.port} "
            f"watching {self.submissions_dir}"
        )
        try:
            self.process_existing()
            while True:
                self.poll_once()
                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            print("[test_pipeline] stopping")
        finally:
            self.stop_server()

    def process_file(self, source_path: Path) -> None:
        started = time.monotonic()
        spec = make_submission_spec(source_path)
        problem = self.problems[spec.problem_id]

        compiled = self.compiler.compile(spec)
        if not compiled.ok:
            self._append_result(
                spec,
                problem,
                verdict="CE",
                passed=0,
                total=len(problem.tests),
                cases_text="",
                started=started,
                message=compiled.log,
            )
            return

        spec.result_dir.mkdir(parents=True, exist_ok=True)
        requests: list[dict[str, Any]] = []
        for case in problem.tests:
            limits = Limits(
                cpu_ms=problem.time_limit_ms,
                mem_bytes=problem.memory_limit_mib << 20,
                out_bytes=case.out_bytes,
            )
            requests.append(
                {
                    "ver": 1,
                    "file_id": spec.stem,
                    "problem_id": spec.problem_id,
                    "test_id": case.case_id,
                    "bin_path": str(spec.bin_path),
                    "input_path": str(case.input_path),
                    "answer_path": str(case.output_path),
                    "result_dir": str(spec.result_dir),
                    "limits": limits.to_json(),
                    "compare_mode": problem.compare_mode,
                }
            )

        assert self.client is not None
        results = self.client.evaluate(requests, workers=self.workers)
        verdict, ordered = self.aggregator.aggregate(problem.tests, results)
        passed = sum(1 for result in ordered if result["verdict"] == "AC")
        cases_text = ";".join(
            f"{result['test_id']}={result['verdict']}" for result in ordered
        )
        message = ""
        if verdict == "SE":
            message = next(
                (
                    str(result.get("error", ""))
                    for result in ordered
                    if result.get("verdict") == "SE" and result.get("error")
                ),
                "",
            )

        self._append_result(
            spec,
            problem,
            verdict=verdict,
            passed=passed,
            total=len(problem.tests),
            cases_text=cases_text,
            started=started,
            message=message,
        )

    def _append_result(
        self,
        spec: SubmissionSpec,
        problem: Problem,
        verdict: str,
        passed: int,
        total: int,
        cases_text: str,
        started: float,
        message: str,
    ) -> None:
        first_message = ""
        if message.strip():
            first_message = message.strip().splitlines()[0]
        self.result_writer.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "source_file": spec.source_path.name,
                "problem_id": problem.problem_id,
                "submission_id": spec.submission_id,
                "verdict": verdict,
                "passed": passed,
                "total": total,
                "cases": cases_text,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "message": first_message or "-",
            }
        )
        print(
            f"[test_pipeline] {spec.source_path.name} -> {verdict} "
            f"({passed}/{total})"
        )
