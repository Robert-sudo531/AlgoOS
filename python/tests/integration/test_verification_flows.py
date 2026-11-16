"""Run the special-flow and reliability-flow verification scripts."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from algoos_testing.local_cluster import ROOT


@pytest.mark.integration
@pytest.mark.parametrize("script", ["scripts/verify_special_flow.py",
                                    "scripts/verify_reliability_flow.py"])
def test_verification_flow(script: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "python")
    result = subprocess.run(
        [sys.executable, str(ROOT / script), "--actor", "pytest"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, (result.stdout[-4000:] + result.stderr[-4000:])
    assert "[通过]" in result.stdout
