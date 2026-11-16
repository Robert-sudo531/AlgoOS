"""Reproducible synthetic workloads for scheduling experiments."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from .simulator import CaseSpec

Profile = Literal["short", "mixed", "heavy_tail", "estimate_error", "burst"]


@dataclass(frozen=True)
class Workload:
    name: str
    cases: tuple[CaseSpec, ...]
    seed: int


SERVICE_CLASSES = {
    "short": (200.0, 600.0),
    "medium": (800.0, 2_500.0),
    "long": (4_000.0, 12_000.0),
}


def _arrival_times(rng: random.Random, count: int, profile: Profile) -> list[float]:
    if profile == "burst":
        # Bursts of ten arrivals separated by quiet gaps.
        times = []
        clock = 0.0
        while len(times) < count:
            for _ in range(10):
                if len(times) >= count:
                    break
                times.append(clock + rng.expovariate(1 / 50.0))
            clock += 2_000.0
        return sorted(times)
    return sorted(rng.expovariate(1 / 400.0) for _ in range(count))


def generate(profile: Profile = "mixed", *, count: int = 200, problems: int = 6,
             cases_per_problem: int = 10, seed: int = 42, transfer_bytes: int = 2 << 20) -> Workload:
    """Generate a fixed, seeded workload; the same seed yields the same trace."""
    rng = random.Random(seed)
    arrivals = _arrival_times(rng, count, profile)
    cases: list[CaseSpec] = []
    for index in range(count):
        problem = index % problems
        case_index = (index // problems) % cases_per_problem + 1
        # Class mix: short tasks dominate, a few long tasks create head-of-line
        # blocking; estimate error workload makes SJF estimates unreliable.
        roll = rng.random()
        if profile == "heavy_tail":
            service = rng.paretovariate(1.4) * 300.0
        elif profile == "short" or roll < 0.65:
            service = rng.uniform(*SERVICE_CLASSES["short"])
        elif roll < 0.92:
            service = rng.uniform(*SERVICE_CLASSES["medium"])
        else:
            service = rng.uniform(*SERVICE_CLASSES["long"])
        estimate = service
        if profile == "estimate_error":
            estimate = service * rng.uniform(0.25, 4.0)
        cases.append(CaseSpec(
            file_id=f"sub-{index:05d}",
            test_id=f"{case_index:02d}",
            sequence=case_index,
            enqueue_seq=index,
            arrival_ms=arrivals[index],
            service_ms=service,
            estimate_ms=estimate,
            bundle_id=f"problem-{problem}",
            transfer_bytes=transfer_bytes,
        ))
    return Workload(name=profile, cases=tuple(cases), seed=seed)
