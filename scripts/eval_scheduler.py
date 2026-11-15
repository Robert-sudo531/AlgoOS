"""First local scheduling policy: FCFS dispatch, round-robin node assignment."""


def round_robin(nodes, sequence):
    if not nodes or len(set(nodes)) != len(nodes) or sequence < 1:
        raise ValueError("expected distinct nodes and a positive case sequence")
    return nodes[(sequence - 1) % len(nodes)]
