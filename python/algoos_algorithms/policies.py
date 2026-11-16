"""Online scheduling policies used by the dispatcher and the simulator.

The unit of scheduling is one test case after compilation.  Every policy is a
pure function over a snapshot of ready cases and node state so the same code
runs in the live control plane and in the offline simulator.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

Strategy = Literal["fcfs", "sjf", "aging_sjf", "earliest_finish", "cache_aware"]

STRATEGIES: tuple[Strategy, ...] = ("fcfs", "sjf", "aging_sjf", "earliest_finish", "cache_aware")


@dataclass(frozen=True)
class ReadyCase:
    file_id: str
    test_id: str
    sequence: int
    enqueue_seq: int
    attempt: int = 1
    estimate_ms: float = 1500.0
    arrival_ms: float = 0.0
    cached_on: tuple[str, ...] = ()
    transfer_bytes: int = 0
    bin_hash: str = ""
    bundle_hash: str = ""

    @property
    def judge_id(self) -> str:
        return f"{self.file_id}:{self.test_id}:{self.attempt}"

    @property
    def order_key(self) -> tuple[int, int, int]:
        return (self.enqueue_seq, self.sequence, self.attempt)


@dataclass(frozen=True)
class NodeState:
    node_id: str
    slots: int = 1
    running: int = 0
    load_score: float = 0.0
    cached_hashes: tuple[str, ...] = ()
    alive: bool = True

    @property
    def free_slots(self) -> int:
        return max(0, self.slots - self.running)


@dataclass(frozen=True)
class Assignment:
    case: ReadyCase
    node_id: str
    score: float
    estimate_ms: float
    cache_hit: bool


AGING_FACTOR = 1.25
TRANSFER_PENALTY_MS = 400.0
DEFAULT_ESTIMATE_MS = 1500.0


def _wait_rounds(case: ReadyCase, now_ms: float) -> int:
    if case.arrival_ms <= 0 or now_ms <= case.arrival_ms:
        return 0
    # One "round" is one default service quantum of waiting.
    return int((now_ms - case.arrival_ms) // DEFAULT_ESTIMATE_MS)


def _effective_estimate(case: ReadyCase, now_ms: float, strategy: Strategy) -> float:
    estimate = max(1.0, case.estimate_ms)
    if strategy == "aging_sjf":
        rounds = _wait_rounds(case, now_ms)
        estimate /= AGING_FACTOR ** rounds
    return estimate


def pick_case(cases: Sequence[ReadyCase], strategy: Strategy, *, now_ms: float,
              nodes: Sequence[NodeState] | None = None) -> ReadyCase:
    """Select one ready case; ties break on enqueue/sequence/attempt."""
    if not cases:
        raise ValueError("no ready cases")
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {strategy}")
    if strategy == "fcfs":
        return min(cases, key=lambda case: case.order_key)
    if strategy == "sjf":
        return min(cases, key=lambda case: (case.estimate_ms, case.order_key))
    if strategy == "aging_sjf":
        return min(cases, key=lambda case: (_effective_estimate(case, now_ms, strategy),
                                            case.order_key))
    if strategy == "earliest_finish":
        node_load = min((node.load_score for node in nodes or () if node.alive and node.free_slots),
                        default=0.0)
        return min(cases, key=lambda case: (case.estimate_ms + node_load, case.order_key))
    # cache_aware: prefer a case whose bin/bundle already lives on a free node.
    def cache_rank(case: ReadyCase) -> tuple[float, tuple[int, int, int]]:
        cached = any(
            node.alive and node.free_slots and _is_cached(case, node)
            for node in nodes or ()
        )
        penalty = 0.0 if cached else TRANSFER_PENALTY_MS
        return (case.estimate_ms + penalty, case.order_key)

    return min(cases, key=cache_rank)


def assign(cases: Sequence[ReadyCase], nodes: Sequence[NodeState], strategy: Strategy,
           *, now_ms: float, default_estimate_ms: float = DEFAULT_ESTIMATE_MS) -> Assignment:
    """Choose both a case and a node.

    ``cache_aware`` minimizes estimated completion (service + transfer + node
    queue); other strategies preserve their selection order and then pick the
    least-loaded reachable node, preferring a cached replica.
    """
    alive = [node for node in nodes if node.alive and node.free_slots > 0]
    if not alive:
        raise ValueError("no node has a free slot")
    if strategy == "cache_aware":
        best: Assignment | None = None
        for case in cases:
            for node in alive:
                cached = _is_cached(case, node)
                transfer = 0.0 if cached else TRANSFER_PENALTY_MS
                estimate = max(1.0, case.estimate_ms or default_estimate_ms)
                score = estimate + transfer + node.load_score
                candidate = Assignment(case, node.node_id, score, estimate, cached)
                if best is None or (score, case.order_key, node.node_id) < (
                        best.score, best.case.order_key, best.node_id):
                    best = candidate
        assert best is not None
        return best

    case = pick_case(cases, strategy, now_ms=now_ms, nodes=nodes)
    def node_key(node: NodeState) -> tuple[float, float, str]:
        cached = _is_cached(case, node)
        transfer = 0.0 if cached else TRANSFER_PENALTY_MS
        return (node.load_score + transfer, -float(node.free_slots), node.node_id)

    node = min(alive, key=node_key)
    cached = _is_cached(case, node)
    estimate = max(1.0, case.estimate_ms or default_estimate_ms)
    score = estimate + (0.0 if cached else TRANSFER_PENALTY_MS) + node.load_score
    return Assignment(case, node.node_id, score, estimate, cached)


def _is_cached(case: ReadyCase, node: NodeState) -> bool:
    if node.node_id in case.cached_on:
        return True
    if case.bin_hash and case.bin_hash in node.cached_hashes:
        return True
    if case.bundle_hash and case.bundle_hash in node.cached_hashes:
        return True
    return any(item in node.cached_hashes for item in case.cached_on)


def iter_assignments(cases: Iterable[ReadyCase], nodes: Sequence[NodeState],
                     strategy: Strategy, *, now_ms: float) -> list[Assignment]:
    """Assign up to one case per free slot for a scheduling round."""
    remaining = list(cases)
    working = [NodeState(**{**node.__dict__}) for node in nodes]
    assignments: list[Assignment] = []
    while remaining and any(node.free_slots for node in working):
        chosen = assign(remaining, working, strategy, now_ms=now_ms)
        assignments.append(chosen)
        remaining = [case for case in remaining if case.judge_id != chosen.case.judge_id]
        for index, node in enumerate(working):
            if node.node_id == chosen.node_id:
                working[index] = NodeState(
                    node_id=node.node_id, slots=node.slots, running=node.running + 1,
                    load_score=node.load_score + chosen.estimate_ms,
                    cached_hashes=node.cached_hashes, alive=node.alive)
    return assignments
