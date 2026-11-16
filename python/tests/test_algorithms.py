"""Scheduling policy, simulator and placement tests."""

from __future__ import annotations

from algoos_algorithms import NodeState, ReadyCase, assign, compute_metrics, simulate, simulated_annealing
from algoos_algorithms.placement import Bundle, Node
from algoos_algorithms.simulator import NodeSpec
from algoos_algorithms.workload import generate


def cases() -> list[ReadyCase]:
    return [
        ReadyCase("f2", "01", 1, 2, estimate_ms=500),
        ReadyCase("f1", "01", 1, 1, estimate_ms=4000),
        ReadyCase("f3", "01", 1, 3, estimate_ms=1000),
    ]


def test_fcfs_follows_enqueue_order() -> None:
    nodes = [NodeState("n1", slots=1)]
    selected = assign(cases(), nodes, "fcfs", now_ms=0)
    assert selected.case.file_id == "f1"


def test_sjf_selects_shortest() -> None:
    nodes = [NodeState("n1", slots=1)]
    selected = assign(cases(), nodes, "sjf", now_ms=0)
    assert selected.case.file_id == "f2"


def test_cache_aware_prefers_cached_node() -> None:
    nodes = [NodeState("n1", slots=1, load_score=0), NodeState("n2", slots=1, load_score=100)]
    cached = ReadyCase("f1", "01", 1, 1, estimate_ms=1000, bin_hash="sha256:" + "a" * 64)
    nodes[1] = NodeState("n2", slots=1, load_score=100,
                         cached_hashes=("sha256:" + "a" * 64,))
    assignment = assign([cached], nodes, "cache_aware", now_ms=0)
    assert assignment.node_id == "n2"
    assert assignment.cache_hit


def test_aging_prevents_starvation() -> None:
    # Epoch-style arrivals: the long task has waited 60 s, the short one is new.
    long_waiting = ReadyCase("long", "01", 1, 1, estimate_ms=5000, arrival_ms=1_000)
    short_new = ReadyCase("short", "01", 1, 100, estimate_ms=3000, arrival_ms=60_000)
    nodes = [NodeState("n1", slots=1)]
    decision = assign([long_waiting, short_new], nodes, "aging_sjf", now_ms=61_000)
    # After 40 aging rounds the long task's effective estimate shrinks below
    # the short task and it must be chosen.
    assert decision.case.file_id == "long"
    fresh = assign([ReadyCase("a", "01", 1, 1, estimate_ms=1000),
                    ReadyCase("b", "01", 1, 2, estimate_ms=2000)],
                   nodes, "aging_sjf", now_ms=0)
    assert fresh.case.file_id == "a"


def test_cache_aware_case_selection_uses_content_hashes() -> None:
    cached_hash = "sha256:" + "a" * 64
    cold_hash = "sha256:" + "b" * 64
    nodes = [NodeState("n1", slots=1, cached_hashes=(cached_hash,))]
    cached = ReadyCase("cached", "01", 1, 2, estimate_ms=1800, bin_hash=cached_hash)
    cold = ReadyCase("cold", "01", 1, 1, estimate_ms=1500, bin_hash=cold_hash)
    assignment = assign([cold, cached], nodes, "cache_aware", now_ms=0)
    assert assignment.case.file_id == "cached"
    assert assignment.cache_hit


def test_simulator_is_deterministic_and_reports_metrics() -> None:
    workload = generate("mixed", count=40, seed=7)
    nodes = [NodeSpec("sim-1", slots=2, cached_bundles=("problem-0",)),
             NodeSpec("sim-2", slots=2, cached_bundles=("problem-1",))]
    first = simulate(workload.cases, nodes, "fcfs")
    second = simulate(workload.cases, nodes, "fcfs")
    assert first.metrics.as_dict() == second.metrics.as_dict()
    assert first.metrics.count == 40
    assert first.metrics.makespan_ms > 0
    assert first.clock_ms == second.clock_ms


def test_fcfs_can_block_short_tasks() -> None:
    mixed = generate("mixed", count=30, seed=11)
    nodes = [NodeSpec("sim-1", slots=1)]
    sjf = simulate(mixed.cases, nodes, "sjf")
    fcfs = simulate(mixed.cases, nodes, "fcfs")
    # SJF is not guaranteed to improve makespan, but on a mixed long/short
    # workload it should reduce average wait in this fixed trace.
    assert sjf.metrics.avg_wait_ms <= fcfs.metrics.avg_wait_ms


def test_metrics_hand_computed() -> None:
    records = [
        {"arrival_ms": 0, "start_ms": 0, "finish_ms": 100, "cache_hit": True},
        {"arrival_ms": 0, "start_ms": 100, "finish_ms": 300, "cache_hit": False},
    ]
    metrics = compute_metrics(records)
    assert metrics.avg_wait_ms == 50
    assert metrics.avg_turnaround_ms == 200
    assert metrics.makespan_ms == 300
    assert metrics.cache_hit_rate == 0.5


def test_simulated_annealing_never_worsens_greedy() -> None:
    bundles = [Bundle("b1", demand=40), Bundle("b2", demand=30), Bundle("b3", demand=20)]
    nodes = [Node("n1", capacity=100), Node("n2", capacity=100), Node("n3", capacity=100)]
    transfer = {("n1", "n2"): 5, ("n2", "n1"): 5, ("n1", "n3"): 9, ("n3", "n1"): 9,
                ("n2", "n3"): 2, ("n3", "n2"): 2, ("n1", "n1"): 0, ("n2", "n2"): 0,
                ("n3", "n3"): 0}
    result = simulated_annealing(bundles, nodes, transfer, replicas=2, iterations=2000,
                                 seed=5)
    assert result.cost <= result.history[0]
    assert len(result.placement) == 3
