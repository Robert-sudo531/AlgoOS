"""Scheduling metrics shared by simulation and cluster experiments."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Metrics:
    count: int
    avg_wait_ms: float
    p50_wait_ms: float
    p95_wait_ms: float
    p99_wait_ms: float
    max_wait_ms: float
    avg_turnaround_ms: float
    makespan_ms: float
    throughput_per_second: float
    cache_hit_rate: float
    fairness_jain: float
    starvation_count: int

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return float(ordered[index])


def compute_metrics(records: Sequence[dict], *, starvation_threshold_ms: float = 60_000,
                    cache_hits: int | None = None) -> Metrics:
    """``records`` need arrival_ms/start_ms/finish_ms and optional cache_hit."""
    waits = [max(0.0, record["start_ms"] - record["arrival_ms"]) for record in records]
    turnarounds = [max(0.0, record["finish_ms"] - record["arrival_ms"]) for record in records]
    if records:
        start = min(record["arrival_ms"] for record in records)
        end = max(record["finish_ms"] for record in records)
        makespan = max(0.0, end - start)
    else:
        makespan = 0.0
    hits = cache_hits if cache_hits is not None else sum(
        1 for record in records if record.get("cache_hit"))
    throughput = (len(records) / (makespan / 1000.0)) if makespan > 0 else 0.0
    # Jain fairness over turnaround times: (sum x)^2 / (n * sum x^2).
    if turnarounds:
        total = sum(turnarounds)
        square = sum(value * value for value in turnarounds)
        fairness = (total * total) / (len(turnarounds) * square) if square else 0.0
    else:
        fairness = 0.0
    return Metrics(
        count=len(records),
        avg_wait_ms=(sum(waits) / len(waits)) if waits else 0.0,
        p50_wait_ms=_percentile(waits, 0.50),
        p95_wait_ms=_percentile(waits, 0.95),
        p99_wait_ms=_percentile(waits, 0.99),
        max_wait_ms=max(waits) if waits else 0.0,
        avg_turnaround_ms=(sum(turnarounds) / len(turnarounds)) if turnarounds else 0.0,
        makespan_ms=makespan,
        throughput_per_second=throughput,
        cache_hit_rate=(hits / len(records)) if records else 0.0,
        fairness_jain=fairness,
        starvation_count=sum(1 for wait in waits if wait >= starvation_threshold_ms),
    )
