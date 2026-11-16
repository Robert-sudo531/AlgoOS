"""Offline replica placement with simulated annealing.

Decision variables: for each ``bundle`` choose a set of nodes that hold a
replica.  The objective is the same shape as the report plan:

    J = alpha * max_load + beta * avg_transfer + gamma * imbalance
        + delta * capacity_violation
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bundle:
    bundle_id: str
    demand: float
    size_bytes: int = 0


@dataclass(frozen=True)
class Node:
    node_id: str
    capacity: float = 100.0
    base_load: float = 0.0


@dataclass
class PlacementResult:
    placement: dict[str, tuple[str, ...]]
    cost: float
    history: list[float] = field(default_factory=list)
    seed: int = 0
    iterations: int = 0


def _loads(placement: dict[str, tuple[str, ...]], bundles: dict[str, Bundle],
           nodes: Sequence[Node]) -> dict[str, float]:
    loads = {node.node_id: node.base_load for node in nodes}
    for bundle_id, replicas in placement.items():
        share = bundles[bundle_id].demand / max(1, len(replicas))
        for node_id in replicas:
            loads[node_id] = loads.get(node_id, 0.0) + share
    return loads


def _objective(placement: dict[str, tuple[str, ...]], bundles: dict[str, Bundle],
               nodes: Sequence[Node], transfer: dict[tuple[str, str], float],
               alpha: float, beta: float, gamma: float, delta: float) -> float:
    loads = _loads(placement, bundles, nodes)
    mean = sum(loads.values()) / len(loads) if loads else 0.0
    variance = sum((value - mean) ** 2 for value in loads.values()) / len(loads) if loads else 0.0
    max_load = max(loads.values(), default=0.0)
    violation = sum(max(0.0, loads[node.node_id] - node.capacity) for node in nodes)
    # Average transfer cost assumes the request lands on a random replica node
    # and pays the mean inter-node cost when that node is not the source.
    average_transfer = 0.0
    pairs = 0
    for bundle_id, replicas in placement.items():
        demand = bundles[bundle_id].demand
        for source in replicas:
            for target in (node.node_id for node in nodes):
                average_transfer += demand * transfer.get((source, target), 0.0)
                pairs += 1
    average_transfer = average_transfer / pairs if pairs else 0.0
    return alpha * max_load + beta * average_transfer + gamma * math.sqrt(variance) + delta * violation


def _initial_placement(bundles: Sequence[Bundle], nodes: Sequence[Node], *, greedy: bool,
                       replicas: int, rng: random.Random) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for bundle in bundles:
        chosen: list[str] = []
        if greedy:
            ordered = sorted(nodes, key=lambda node: node.capacity - node.base_load,
                             reverse=True)
            chosen = [node.node_id for node in ordered[:replicas]]
        else:
            chosen = [node.node_id for node in rng.sample(list(nodes), k=min(replicas, len(nodes)))]
        result[bundle.bundle_id] = tuple(sorted(chosen))
    return result


def simulated_annealing(bundles: Sequence[Bundle], nodes: Sequence[Node],
                        transfer: dict[tuple[str, str], float], *, replicas: int = 1,
                        iterations: int = 20_000, seed: int = 42,
                        initial_temperature: float = 50.0, cooling: float = 0.995,
                        alpha: float = 1.0, beta: float = 1.0, gamma: float = 0.5,
                        delta: float = 10.0) -> PlacementResult:
    """Return the best placement found; deterministic for a fixed seed."""
    if not bundles or not nodes:
        raise ValueError("placement requires bundles and nodes")
    rng = random.Random(seed)
    bundle_map = {bundle.bundle_id: bundle for bundle in bundles}
    placement = _initial_placement(bundles, nodes, greedy=True, replicas=replicas, rng=rng)
    current = _objective(placement, bundle_map, nodes, transfer, alpha, beta, gamma, delta)
    best = dict(placement)
    best_cost = current
    history = [current]
    temperature = initial_temperature
    node_ids = [node.node_id for node in nodes]

    def neighbours(state: dict[str, tuple[str, ...]]) -> Iterable[dict[str, tuple[str, ...]]]:
        for bundle_id, held in state.items():
            for source in held:
                for target in node_ids:
                    if target == source:
                        continue
                    replica = sorted((set(held) - {source}) | {target})
                    if not replica:
                        continue
                    candidate = dict(state)
                    candidate[bundle_id] = tuple(replica)
                    yield candidate

    for _ in range(iterations):
        candidate = next(neighbours(placement), None)
        if candidate is None:
            break
        cost = _objective(candidate, bundle_map, nodes, transfer, alpha, beta, gamma, delta)
        delta_cost = cost - current
        if delta_cost <= 0 or rng.random() < math.exp(-delta_cost / max(temperature, 1e-9)):
            placement = candidate
            current = cost
            if cost < best_cost:
                best = dict(placement)
                best_cost = cost
        temperature *= cooling
        history.append(best_cost)
    return PlacementResult(placement=best, cost=best_cost, history=history, seed=seed,
                           iterations=iterations)
