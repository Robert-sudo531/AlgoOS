"""Discrete-event scheduling simulator.

The simulator advances a virtual clock; it never sleeps.  It models node
slots, cached bundles (affinity), transfer cost on a cache miss and the same
online policies used by the live dispatcher.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .metrics import Metrics, compute_metrics
from .policies import ReadyCase, Strategy, assign


@dataclass(frozen=True)
class NodeSpec:
    node_id: str
    slots: int = 1
    cached_bundles: tuple[str, ...] = ()
    bandwidth_bytes_per_ms: float = 20_000.0  # 20 MB/s


@dataclass(frozen=True)
class CaseSpec:
    file_id: str
    test_id: str
    sequence: int
    enqueue_seq: int
    arrival_ms: float
    service_ms: float
    estimate_ms: float
    bundle_id: str = ""
    transfer_bytes: int = 0
    attempt: int = 1

    def ready(self) -> ReadyCase:
        return ReadyCase(
            file_id=self.file_id, test_id=self.test_id, sequence=self.sequence,
            enqueue_seq=self.enqueue_seq, attempt=self.attempt,
            estimate_ms=self.estimate_ms, arrival_ms=self.arrival_ms,
            transfer_bytes=self.transfer_bytes)


@dataclass
class SimulationResult:
    records: list[dict]
    metrics: Metrics
    clock_ms: float

    def as_dict(self) -> dict:
        return {"records": self.records, "metrics": self.metrics.as_dict(),
                "clock_ms": self.clock_ms}


def simulate(cases: Sequence[CaseSpec], nodes: Sequence[NodeSpec], strategy: Strategy,
             *, horizon_ms: float | None = None) -> SimulationResult:
    if not nodes:
        raise ValueError("simulation requires at least one node")
    pending = sorted(cases, key=lambda case: (case.arrival_ms, case.enqueue_seq, case.sequence))
    ready: list[CaseSpec] = []
    running: list[dict] = []
    node_cache = {node.node_id: set(node.cached_bundles) for node in nodes}
    records: list[dict] = []
    clock = 0.0

    def node_states():
        from .policies import NodeState

        states = []
        for node in nodes:
            active = [job for job in running if job["node_id"] == node.node_id]
            queued_ms = sum(max(0.0, job["finish_ms"] - clock) for job in active)
            states.append(NodeState(
                node_id=node.node_id, slots=node.slots, running=len(active),
                load_score=queued_ms,
                cached_hashes=tuple(f"bundle:{name}" for name in node_cache[node.node_id]),
                alive=True))
        return states

    while pending or ready or running:
        next_arrival = pending[0].arrival_ms if pending else float("inf")
        next_completion = min((job["finish_ms"] for job in running), default=float("inf"))
        clock = min(next_arrival, next_completion)
        if not math.isfinite(clock):
            break
        if horizon_ms is not None and clock > horizon_ms:
            break
        finished = [job for job in running if job["finish_ms"] <= clock + 1e-9]
        for job in finished:
            records.append({
                "file_id": job["case"].file_id, "test_id": job["case"].test_id,
                "sequence": job["case"].sequence, "node_id": job["node_id"],
                "arrival_ms": job["case"].arrival_ms, "start_ms": job["start_ms"],
                "finish_ms": job["finish_ms"],
                "wait_ms": job["start_ms"] - job["case"].arrival_ms,
                "turnaround_ms": job["finish_ms"] - job["case"].arrival_ms,
                "transfer_ms": job["transfer_ms"], "cache_hit": job["cache_hit"],
                "service_ms": job["service_ms"],
            })
            running.remove(job)
        while pending and pending[0].arrival_ms <= clock + 1e-9:
            ready.append(pending.pop(0))

        while ready:
            states = node_states()
            if not any(state.free_slots for state in states):
                break
            assignment = assign([case.ready() for case in ready], states, strategy,
                                now_ms=clock)
            case = next(case for case in ready if case.file_id == assignment.case.file_id
                        and case.test_id == assignment.case.test_id)
            node = next(node for node in nodes if node.node_id == assignment.node_id)
            bundle = case.bundle_id or case.file_id
            cached = bundle in node_cache[node.node_id]
            transfer_ms = 0.0 if cached else (case.transfer_bytes /
                                              max(1.0, node.bandwidth_bytes_per_ms))
            service = case.service_ms + transfer_ms
            running.append({
                "case": case, "node_id": node.node_id, "start_ms": clock,
                "service_ms": service, "finish_ms": clock + service,
                "transfer_ms": transfer_ms, "cache_hit": cached,
            })
            node_cache[node.node_id].add(bundle)
            ready.remove(case)
        if not ready and not running and pending:
            # Jump to the next arrival when the cluster is idle.
            clock = max(clock, pending[0].arrival_ms)

    metrics = compute_metrics(records, cache_hits=sum(1 for r in records if r["cache_hit"]))
    return SimulationResult(records=records, metrics=metrics, clock_ms=clock)
