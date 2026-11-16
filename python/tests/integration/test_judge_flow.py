"""Real local cluster: AC/CE/WA/RE/TLE through the distributed API."""

from __future__ import annotations

from pathlib import Path

import pytest
from algoos_testing.local_cluster import ROOT, LocalCluster


def source(name: str) -> str:
    return (ROOT / "examples" / name).read_text(encoding="utf-8")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("example", "expected"),
    [
        ("a_plus_b.cpp", "AC"),
        ("compile_error.cpp", "CE"),
        ("wrong_answer.cpp", "WA"),
        ("runtime_error.cpp", "RE"),
        ("time_limit.cpp", "TLE"),
        ("output_limit.cpp", "WR"),
    ],
)
def test_judge_flow(tmp_path: Path, example: str, expected: str) -> None:
    with LocalCluster(problems=("a_plus_b",), strategy="fcfs",
                 log_dir=tmp_path / "logs") as cluster:
        file_id = cluster.submit("a_plus_b", source(example),
                                 evaluation_mode="all_cases", strategy="fcfs")
        view = cluster.wait_cases_terminal(file_id, timeout=60)
        assert view["status"] in {"FINAL", "CE"}, view
        assert view["verdict"] == expected, view
        if expected in {"CE"}:
            assert view["per_test"] == []
            assert view["compile_log"]
        else:
            assert view["per_test"], view
            assert all(point["verdict"] == expected for point in view["per_test"]), view


@pytest.mark.integration
def test_cache_hit_and_duplicate_result(tmp_path: Path) -> None:
    with LocalCluster(problems=("a_plus_b",), strategy="cache_aware",
                 log_dir=tmp_path / "logs") as cluster:
        views = []
        for _ in range(3):
            file_id = cluster.submit("a_plus_b", source("a_plus_b.cpp"),
                                     evaluation_mode="all_cases")
            views.append(cluster.wait_cases_terminal(file_id, timeout=60))
        assert all(point["cache_hit"] for point in views[-1]["per_test"])
        file_id = views[0]["file_id"]
        _judge_id, result = cluster.query(
            "SELECT judge_id, result FROM eval_results WHERE file_id=%s "
            "ORDER BY sequence LIMIT 1", (file_id,))[0]
        before = cluster.query("SELECT count(*) FROM eval_results WHERE file_id=%s",
                               (file_id,))[0][0]
        duplicate = dict(result)
        duplicate["msg_id"] += ":pytest-duplicate"
        cluster.publish("algoos.eval.result", duplicate)
        conflicting = dict(result)
        conflicting["msg_id"] += ":pytest-conflict"
        conflicting["verdict"] = "WA"
        conflicting["reason"] = "ANSWER_MISMATCH"
        cluster.publish("algoos.eval.result", conflicting)
        import time

        time.sleep(1.0)
        after = cluster.query("SELECT count(*) FROM eval_results WHERE file_id=%s",
                              (file_id,))[0][0]
        assert after == before
        assert cluster.view(file_id)["verdict"] == views[0]["verdict"]
