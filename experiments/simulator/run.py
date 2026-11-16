"""Offline discrete-event simulation runner (never presented as cluster data)."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from algoos_algorithms import simulate
from algoos_algorithms.simulator import NodeSpec
from algoos_algorithms.workload import generate

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="mixed",
                        choices=["short", "mixed", "heavy_tail", "estimate_error", "burst"])
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--nodes", type=int, default=2)
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--problems", type=int, default=6)
    parser.add_argument("--strategies", nargs="+",
                        default=["fcfs", "sjf", "aging_sjf", "earliest_finish",
                                 "cache_aware"])
    args = parser.parse_args(argv)
    workload = generate(args.profile, count=args.count, problems=args.problems,
                        seed=args.seed)
    nodes = [
        NodeSpec(node_id=f"sim-{index + 1:02d}", slots=args.slots,
                 cached_bundles=tuple(f"problem-{problem}" for problem in
                                      range(args.problems) if problem % args.nodes == index))
        for index in range(args.nodes)
    ]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"_simulation_{args.profile}"
    out = ROOT / "artifacts" / "experiments" / run_id
    if out.exists():
        out = out.with_name(f"{run_id}_{uuid4().hex[:6]}")
    out.mkdir(parents=True)
    summary = {"data_source": "simulation", "profile": args.profile,
               "seed": args.seed, "count": args.count, "nodes": args.nodes,
               "slots": args.slots, "strategies": {}}
    for strategy in args.strategies:
        result = simulate(workload.cases, nodes, strategy)
        summary["strategies"][strategy] = result.metrics.as_dict()
        path = out / f"{strategy}.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(result.records[0].keys()))
            writer.writeheader()
            writer.writerows(result.records)
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "run_id": out.name,
        "data_source": "simulation",
        "kind": "scheduling_simulation",
        "profile": args.profile,
        "seed": args.seed,
        "count": args.count,
        "nodes": args.nodes,
        "slots": args.slots,
        "note": "Discrete-event simulation; not cluster or production measurement.",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"模拟证据: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
