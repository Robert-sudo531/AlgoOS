"""Summarise cluster or simulation experiment evidence as Markdown."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
METRIC_KEYS = ["count", "avg_wait_ms", "p50_wait_ms", "p95_wait_ms", "p99_wait_ms",
               "max_wait_ms", "avg_turnaround_ms", "makespan_ms",
               "throughput_per_second", "cache_hit_rate", "fairness_jain",
               "starvation_count"]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cluster_report(run: Path) -> str:
    manifest = _read(run / "manifest.json")
    summary = _read(run / "summary.json")
    rows = list(csv.DictReader((run / "runs.csv").open(encoding="utf-8")))
    metrics = summary["metrics"]
    lines = [f"# Cluster experiment: {run.name}", "",
             f"- data source: `{manifest['data_source']}`",
             f"- strategy: `{manifest['strategy']}`",
             f"- records: {manifest['record_count']}",
             f"- correctness: {summary['correctness']['matched']}/"
             f"{summary['correctness']['expected']}",
             f"- note: {manifest['note']}", "", "## Metrics", "",
             "| metric | value |", "|---|---:|"]
    for key in METRIC_KEYS:
        value = metrics.get(key)
        lines.append(f"| {key} | {value:.3f} |" if isinstance(value, float)
                     else f"| {key} | {value} |")
    lines += ["", "## Submissions", "",
              "| # | fixture | expected | verdict | wait ms | turnaround ms | nodes | cache hits |",
              "|---:|---|---|---|---:|---:|---|---:|"]
    for row in rows:
        lines.append(f"| {row['index']} | {row['fixture']} | {row['expected']} | "
                     f"{row['verdict'] or row['status']} | {row['wait_ms']} | "
                     f"{row['turnaround_ms']} | {row['nodes']} | {row['cache_hits']} |")
    return "\n".join(lines) + "\n"


def simulation_report(run: Path) -> str:
    summary = _read(run / "summary.json")
    lines = [f"# Simulation experiment: {run.name}", "",
             f"- data source: `{summary['data_source']}`",
             f"- profile: `{summary['profile']}`; seed: {summary['seed']}",
             f"- cases: {summary['count']}; nodes: {summary['nodes']}; slots: {summary['slots']}",
             "", "| strategy | avg wait | p95 wait | max wait | avg turnaround | throughput | cache hit | Jain |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for strategy, metrics in summary["strategies"].items():
        lines.append(f"| {strategy} | {metrics['avg_wait_ms']:.1f} | "
                     f"{metrics['p95_wait_ms']:.1f} | {metrics['max_wait_ms']:.1f} | "
                     f"{metrics['avg_turnaround_ms']:.1f} | "
                     f"{metrics['throughput_per_second']:.2f} | "
                     f"{metrics['cache_hit_rate']:.3f} | {metrics['fairness_jain']:.3f} |")
    lines += ["", "> Simulation data only; not a real-cluster performance result."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path,
                        help="artifacts/experiments/<run> directories")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    reports = []
    for run in args.runs:
        if (run / "summary.json").is_file() and (run / "manifest.json").is_file():
            text = cluster_report(run)
        elif (run / "summary.json").is_file():
            text = simulation_report(run)
        else:
            raise SystemExit(f"missing summary.json: {run}")
        reports.append(text)
        print(text)
    out = args.out or args.runs[0] / "report.md"
    out.write_text("\n\n---\n\n".join(reports), encoding="utf-8")
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
