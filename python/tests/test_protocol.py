"""Protocol contract tests: golden messages, negative fixtures, schemas."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from algoos_protocol import ContractError, dumps, load_policy, loads, normalize

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "protocol" / "golden"
FIXTURES = ROOT / "protocol" / "fixtures"
SCHEMAS = ROOT / "protocol" / "schemas"
POLICY = load_policy()


@pytest.mark.parametrize("path", sorted(GOLDEN.glob("*.json")))
def test_golden_messages_roundtrip(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if path.name == "heartbeat.json":
        pytest.skip("heartbeat uses the standalone model, not the message codec")
    normalized = normalize(payload, POLICY)
    once = dumps(normalized, POLICY)
    twice = dumps(loads(once, POLICY), POLICY)
    assert once == twice


@pytest.mark.parametrize("path", sorted(GOLDEN.glob("*.json")))
def test_golden_messages_match_json_schema(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if path.name == "heartbeat.json":
        schema_name = "heartbeat"
    elif payload.get("type") == "eval_result" and payload.get("stage") == "compile":
        schema_name = "compile_result"
    elif payload.get("type") == "eval_result":
        schema_name = "eval_result"
    elif payload.get("type") == "cancel":
        schema_name = "cancel"
    elif payload.get("type") == "heartbeat":
        schema_name = "heartbeat"
    else:
        schema_name = payload["type"]
    schema = json.loads((SCHEMAS / f"{schema_name}.json").read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")))
def test_invalid_fixtures_rejected(path: Path) -> None:
    with pytest.raises((ContractError, ValueError, json.JSONDecodeError)):
        loads(path.read_text(encoding="utf-8"), POLICY)


def test_unknown_fields_are_preserved() -> None:
    payload = json.loads((GOLDEN / "eval_task.json").read_text(encoding="utf-8"))
    payload["future_field"] = {"nested": [1, 2, 3]}
    normalized = normalize(payload, POLICY)
    assert normalized["future_field"] == {"nested": [1, 2, 3]}
    assert json.loads(dumps(normalized, POLICY))["future_field"] == {"nested": [1, 2, 3]}


def test_default_status_applied_only_for_results() -> None:
    payload = json.loads((GOLDEN / "eval_result.json").read_text(encoding="utf-8"))
    payload.pop("status")
    normalized = normalize(payload, POLICY)
    assert normalized["status"] == "FINAL"


def test_boolean_is_not_an_integer() -> None:
    payload = json.loads((GOLDEN / "eval_task.json").read_text(encoding="utf-8"))
    payload["sequence"] = True
    with pytest.raises(ContractError):
        normalize(payload, POLICY)


def test_policy_exposes_all_six_topics() -> None:
    transport = POLICY["transport"]
    for key in ("compile_task_subject", "compile_result_subject", "eval_task_subject_template",
                "eval_result_subject", "special_task_subject",
                "special_result_subject_template", "cancel_subject", "fallback_subject"):
        assert key in transport
