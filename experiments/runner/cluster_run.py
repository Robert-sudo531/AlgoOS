"""Run a fixed submission trace against the real local AlgoOS cluster.

This is the *cluster* experiment runner.  It uses the same API, scheduler,
workers and object store as production; measurements are labelled
``data_source=cluster`` and must not be mixed with simulator output.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from algoos_algorithms.metrics import compute_metrics
from algoos_testing.local_cluster import ROOT, LocalCluster

DEFAULT_SOURCES = [
    {"problem_id": "a_plus_b", "file": "a_plus_b.cpp", "expected": "AC"},
    {"problem_id": "a_plus_b", "file": "wrong_answer.cpp", "expected": "WA"},
    {"problem_id": "a_plus_b", "file": "compile_error.cpp", "expected": "CE"},
    {"problem_id": "a_plus_b", "file": "runtime_error.cpp", "expected": "RE"},
    {"problem_id": "a_plus_b", "file": "time_limit.cpp", "expected": "TLE"},
    {"problem_id": "a_plus_b", "file": "output_limit.cpp", "expected": "WR"},
    {"problem_id": "lazy_gold", "file": "lazy_gold.cpp", "expected": "AC"},
]


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                       text=True).strip()
    except Exception:
        return "unknown"


def _load_profile(path: Path | None) -> dict:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _entries(profile: dict, workload: dict | None, count: int) -> list[dict]:
    if workload is not None:
        entries = []
        base = workload["entries"]
        for index in range(count):
            entry = dict(base[index % len(base)])
            entry["offset_ms"] = int(entry.get("offset_ms", 0)) + (
                index // len(base)) * 1000
            entries.append(entry)
        return entries
    sources = profile.get("sources") or DEFAULT_SOURCES
    gap = int(profile.get("arrival_gap_ms", 100))
    return [{**sources[index % len(sources)], "offset_ms": index * gap}
            for index in range(count)]


def _flat(record: dict) -> dict:
    view = record.get("view") or {}
    points = view.get("per_test") or []
    arrival = record.get("arrival_ms") or 0.0
    start = record.get("start_ms") or record.get("finish_ms") or arrival
    finish = record.get("finish_ms") or start
    nodes = sorted({point.get("node_id") for point in points if point.get("node_id")})
    return {
        "index": record["index"],
        "file_id": record["file_id"],
        "problem_id": record["problem_id"],
        "fixture": record["fixture"],
        "expected": record.get("expected"),
        "verdict": view.get("verdict"),
        "status": view.get("status"),
        "arrival_ms": round(arrival, 3),
        "start_ms": round(start, 3),
        "finish_ms": round(finish, 3),
        "wait_ms": round(max(0.0, start - arrival), 3),
        "turnaround_ms": round(max(0.0, finish - arrival), 3),
        "dispatched_tests": len(points),
        "cache_hits": sum(1 for point in points if point.get("cache_hit")),
        "nodes": ",".join(nodes),
        "error": record.get("error", ""),
    }


def _write_csv(path: Path, records: list[dict]) -> None:
    fields = ["index", "file_id", "problem_id", "fixture", "expected", "verdict",
              "status", "arrival_ms", "start_ms", "finish_ms", "wait_ms",
              "turnaround_ms", "dispatched_tests", "cache_hits", "nodes", "error"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key, "") for key in fields})


def _write_outputs(out: Path, flat: list[dict], raw: list[dict], strategy: str,
                   profile: dict, workload: dict | None, actor: str,
                   started: float) -> Path:
    metrics = compute_metrics([
        {"arrival_ms": record["arrival_ms"], "start_ms": record["start_ms"],
         "finish_ms": record["finish_ms"], "cache_hit": record["cache_hits"] > 0}
        for record in flat
    ])
    matched = sum(1 for record in flat
                  if record["expected"] and record["verdict"] == record["expected"])
    expected_count = sum(1 for record in flat if record["expected"])
    verdict_counts: dict[str, int] = {}
    for record in flat:
        key = str(record["verdict"] or record["status"])
        verdict_counts[key] = verdict_counts.get(key, 0) + 1
    _write_csv(out / "runs.csv", flat)
    manifest = {
        "run_id": out.name,
        "data_source": "cluster",
        "actor": actor,
        "strategy": strategy,
        "profile": profile,
        "workload": workload,
        "git_commit": _git_commit(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "started_at": datetime.now(UTC).isoformat(),
        "duration_s": round(time.monotonic() - started, 2),
        "record_count": len(flat),
        "verdict_counts": verdict_counts,
        "correctness": {
            "matched": matched,
            "expected": expected_count,
            "rate": (matched / expected_count) if expected_count else None,
        },
        "metrics": metrics.as_dict(),
        "raw_views": raw,
        "note": "Local multi-process cluster; not a cross-machine or production result.",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "summary.json").write_text(
        json.dumps({"metrics": metrics.as_dict(),
                    "verdict_counts": verdict_counts,
                    "correctness": manifest["correctness"]},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def run(profile: dict, *, count: int, strategy: str, gap_ms: int | None,
        workload: dict | None, actor: str) -> Path:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"_cluster_{strategy}"
    out = ROOT / "artifacts" / "experiments" / run_id
    if out.exists():
        out = out.with_name(f"{run_id}_{uuid4().hex[:6]}")
    out.mkdir(parents=True)
    entries = _entries(profile, workload, count)
    if gap_ms is not None:
        for index, entry in enumerate(entries):
            entry["offset_ms"] = index * gap_ms
    problems = sorted({entry["problem_id"] for entry in entries})
    started = time.monotonic()
    cluster: LocalCluster | None = None
    finished: list[dict] = []
    try:
        cluster = LocalCluster(problems=tuple(problems), strategy=strategy,
                          log_dir=out / "logs")
        cluster.start()
        pending: dict[str, dict] = {}
        trace_start = time.monotonic()
        for index, entry in enumerate(entries):
            due = trace_start + float(entry.get("offset_ms", 0)) / 1000.0
            while time.monotonic() < due:
                time.sleep(min(0.05, max(0.0, due - time.monotonic())))
            fixture = entry["file"]
            source = (ROOT / "examples" / fixture).read_text(encoding="utf-8")
            file_id = cluster.submit(entry["problem_id"], source,
                                     evaluation_mode="all_cases", strategy=strategy)
            pending[file_id] = {
                "index": index, "file_id": file_id, "problem_id": entry["problem_id"],
                "fixture": fixture, "expected": entry.get("expected"),
                "arrival_ms": time.monotonic() * 1000, "start_ms": None,
            }
        deadline = time.monotonic() + 300
        while pending and time.monotonic() < deadline:
            for file_id, record in list(pending.items()):
                view = cluster.view(file_id)
                if record["start_ms"] is None and any(
                        point["state"] == "DISPATCHED" for point in view["per_test"]):
                    record["start_ms"] = time.monotonic() * 1000
                if view["status"] in {"FINAL", "CE", "CANCELLED"}:
                    record["finish_ms"] = time.monotonic() * 1000
                    record["view"] = view
                    finished.append(pending.pop(file_id))
            time.sleep(0.1)
        for record in pending.values():
            record["finish_ms"] = time.monotonic() * 1000
            record["view"] = cluster.view(record["file_id"])
            record["error"] = "experiment timeout"
            finished.append(record)
        raw = sorted(finished, key=lambda record: record["index"])
        flat = [_flat(record) for record in raw]
    finally:
        if cluster is not None:
            cluster.stop()
    return _write_outputs(out, flat, raw, strategy, profile, workload, actor, started)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-config", type=Path,
                        default=ROOT / "experiments/profiles/cluster_mixed.json")
    parser.add_argument("--workload", type=Path, help="fixed workload JSON")
    parser.add_argument("--count", type=int)
    parser.add_argument("--strategy")
    parser.add_argument("--gap-ms", type=int)
    parser.add_argument("--actor", default=os.environ.get("USER", "unknown"))
    args = parser.parse_args(argv)
    profile = _load_profile(args.profile_config)
    workload = (json.loads(args.workload.read_text(encoding="utf-8"))
                if args.workload else None)
    count = args.count or int(profile.get("count", 12))
    strategy = args.strategy or profile.get("strategy", "cache_aware")
    out = run(profile, count=count, strategy=strategy, gap_ms=args.gap_ms,
              workload=workload, actor=args.actor)
    print(f"运行证据: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
