"""Offline simulated-annealing replica placement experiment."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from algoos_algorithms.placement import Bundle, Node, simulated_annealing

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path,
                        default=ROOT / "experiments/profiles/sa_placement.json")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    bundles = [Bundle(**item) for item in profile["bundles"]]
    nodes = [Node(**item) for item in profile["nodes"]]
    transfer = {}
    for key, value in profile["transfer"].items():
        source, target = key.split(":", 1)
        transfer[(source, target)] = float(value)
    result = simulated_annealing(
        bundles, nodes, transfer, replicas=int(profile.get("replicas", 1)),
        iterations=int(profile.get("iterations", 20000)),
        seed=int(profile.get("seed", 42)))
    default_name = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_placement_sa"
    out = args.out or (ROOT / "artifacts" / "experiments" / default_name)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"data_source": "simulation", "profile": profile,
               "placement": {key: list(value) for key, value in result.placement.items()},
               "cost": result.cost, "seed": result.seed,
               "iterations": result.iterations,
               "history": result.history[:: max(1, len(result.history) // 200)]}
    (out / "placement.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "run_id": out.name,
        "data_source": "simulation",
        "kind": "replica_placement",
        "seed": result.seed,
        "iterations": result.iterations,
        "cost": result.cost,
        "note": "Simulated annealing over a synthetic transfer matrix; not a cluster measurement.",
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (out / "placement.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["bundle_id", "replicas", "cost"])
        for bundle_id, replicas in result.placement.items():
            writer.writerow([bundle_id, ",".join(replicas), result.cost])
    print(f"放置证据: {out.relative_to(ROOT)} cost={result.cost:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
