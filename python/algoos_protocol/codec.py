"""Strict v1 wire codec and policy checks.

This module is the Python side of the control contract.  C++ workers parse the
same JSON and validate the fields they consume; the schemas under
``protocol/schemas`` are exported from these Pydantic models.
"""

from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import MessageBase, message_model

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "protocol" / "policy_v1.json"


class ContractError(ValueError):
    """Raised when a payload violates the v1 contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load_policy(path: Path | str | None = None) -> dict[str, Any]:
    policy_path = Path(path) if path is not None else DEFAULT_POLICY
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    require(policy.get("ver") == 1, "policy ver must be 1")
    require(policy.get("policy_id") == "algoos_distributed_v1", "unexpected policy id")
    return policy


def _reject_constant(value: str) -> None:
    raise ContractError(f"non-finite JSON number: {value}")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        require("\0" not in key, "NUL in JSON key")
        result[key] = value
    return result


INTEGER_FIELDS = {
    "ver", "attempt", "sequence", "enqueue_seq", "cancel_generation", "sequence_boundary",
    "cpu_ms", "wall_ms", "mem_bytes", "out_bytes", "exit_code", "signal", "deadline_ms",
    "slots_total", "slots_used", "sent_at_ms",
}


def check_integer_fields(value: Any, depth: int = 0) -> None:
    require(depth < 64, "JSON nesting too deep")
    if type(value) is dict:
        for key, item in value.items():
            if key in INTEGER_FIELDS and type(item) is not int and item is not None:
                raise ContractError(f"{key} must be an integer, not {type(item).__name__}")
            check_integer_fields(item, depth + 1)
    elif type(value) is list:
        for item in value:
            check_integer_fields(item, depth + 1)


def check_tree(value: Any, depth: int = 0) -> None:
    require(depth < 64, "JSON nesting too deep")
    if type(value) is dict:
        for key, item in value.items():
            require(type(key) is str and "\0" not in key, "JSON keys must be strings without NUL")
            check_tree(item, depth + 1)
    elif type(value) is list:
        for item in value:
            check_tree(item, depth + 1)
    elif type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError as error:  # pragma: no cover - Python strings are Unicode
            raise ContractError("invalid Unicode string") from error
    elif type(value) is int:
        require(-(2**63) <= value <= 2**63 - 1, "integer outside signed 64-bit range")
    elif type(value) is float:
        require(math.isfinite(value), "NaN/Infinity is not valid JSON")
    else:
        require(value is None or type(value) is bool, "unsupported JSON value")


def _validate_node(policy: dict[str, Any], value: str, label: str) -> None:
    require(re.fullmatch(policy["transport"]["node_id_pattern"], value) is not None,
            f"{label} does not match node id pattern")


def _validate_policy(message: dict[str, Any], policy: dict[str, Any]) -> None:
    require(message["ver"] == policy["ver"], "unsupported protocol version")
    require(message["type"] in policy["message_types"], f"unsupported message type: {message['type']}")
    timestamp = message["emitted_at"]
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", timestamp)
            is not None, "emitted_at must be an ISO-8601 UTC timestamp")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError("emitted_at is not a valid date") from error

    kind = message["type"]
    if kind in {"compile_task", "eval_task", "special_task", "special_result", "fallback"}:
        require(message.get("problem_id"), f"{kind} requires problem_id")

    if kind == "compile_task":
        profile = policy["compiler_profiles"].get(message["compiler_profile"])
        require(profile is not None, "unknown compiler_profile")
        for key, value in profile["limits"].items():
            require(message["limits"][key] == value, f"compile limit mismatch: {key}")

    if kind == "eval_task":
        for key in ("judge_mode", "compare_mode", "evaluation_mode"):
            require(message[key] in policy["modes"][key], f"unsupported {key}: {message[key]}")
        _validate_node(policy, message["target_node_id"], "target_node_id")
        require(message["limits"]["wall_ms"] >= message["limits"]["cpu_ms"],
                "wall_ms must be >= cpu_ms")
        require((message.get("judge_mode") == "special")
                == (message.get("compare_mode") == "special"),
                "judge_mode and compare_mode must agree")
        if message.get("judge_mode") == "special":
            require(message.get("answer_hash") and message.get("checker_hash"),
                    "special tasks require answer_hash and checker_hash")

    if kind == "eval_result":
        stage = message.get("stage")
        require(stage in policy["result"]["sources_by_stage"], "unsupported result stage")
        require(message["source"] in policy["result"]["sources_by_stage"][stage],
                f"source not allowed for stage {stage}")
        _validate_node(policy, message["node_id"], "node_id")
        if message["status"] == "COMPILED":
            require(stage == "compile", "only compile results may be COMPILED")
            require(message.get("verdict") is None, "COMPILED must not carry a verdict")
            require(message.get("reason") == "NONE", "COMPILED reason must be NONE")
        else:
            verdict = message.get("verdict")
            allowed = policy["result"]["final_verdicts_by_stage"][stage]
            require(verdict in allowed, f"verdict {verdict!r} not allowed for {stage}")
            reasons = policy["result"]["reason_by_verdict"].get(verdict, [])
            require(message.get("reason") in reasons,
                    f"reason {message.get('reason')!r} does not match verdict {verdict}")

    if kind == "special_task":
        transport = policy["transport"]
        require(message["fallback_topic"] == transport["fallback_subject"], "unexpected fallback topic")
        reply = message["reply_to"]
        _validate_node(policy, reply["eval_node_id"], "reply_to.eval_node_id")
        expected = transport["special_result_subject_template"].format(
            target_eval_node=reply["eval_node_id"])
        require(reply["reply_topic"] == expected, "reply_topic does not match eval node")
        require(message["checker_hash"].startswith("sha256:"), "invalid checker hash")

    if kind == "special_result":
        require(message["source"] == "special", "special result source must be special")
        _validate_node(policy, message["target_eval_node"], "target_eval_node")
        require(message["verdict"] in {"AC", "WA", "SE"}, "unsupported special verdict")

    if kind == "cancel":
        require(message["reason"] in {"USER_CANCELLED", "SHORT_CIRCUITED"},
                "cancel reason must be USER_CANCELLED or SHORT_CIRCUITED")
        if message["scope"] == "case":
            require(message.get("judge_id"), "case cancellation requires judge_id")
        if message.get("sequence_boundary") is not None:
            require(message["sequence_boundary"] >= 1, "invalid sequence boundary")

    if kind == "fallback":
        require(message["source"] == "special", "fallback source must be special")
        _validate_node(policy, message["reply_to"]["eval_node_id"], "reply_to.eval_node_id")


def normalize(message: Any, policy: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a new dict with defaults applied.

    Unknown fields are preserved.  The input object is never mutated.
    """
    check_tree(message)
    check_integer_fields(message)
    require(type(message) is dict, "message must be a JSON object")
    data = copy.deepcopy(message)
    if data.get("type") == "eval_result" and "status" not in data:
        data["status"] = policy["result"]["default_status"]

    model_type = message_model(data)
    try:
        model = model_type(**data)
    except Exception as error:  # pydantic ValidationError and ValueErrors
        raise ContractError(str(error)) from error
    result = model.model_dump(mode="json")
    _validate_policy(result, policy)
    return result


def parse_model(message: Any, policy: dict[str, Any]) -> MessageBase:
    data = normalize(message, policy)
    return message_model(data)(**data)


def loads(payload: bytes | str, policy: dict[str, Any]) -> dict[str, Any]:
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        message = json.loads(payload, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except ContractError:
        raise
    except (ValueError, UnicodeError) as error:
        raise ContractError(f"JSON parse error: {error}") from error
    return normalize(message, policy)


def dumps(message: Any, policy: dict[str, Any]) -> str:
    normalized = normalize(message, policy)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def canonical_json(value: Any) -> str:
    check_tree(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def utc_now() -> str:
    return datetime.now(tz=None).astimezone().strftime("%Y-%m-%dT%H:%M:%SZ")


def new_msg_id(prefix: str = "m") -> str:
    return f"{prefix}_{uuid4().hex}"


def hash_bytes(data: bytes) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_key(value: Any) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_hash(value: str) -> str:
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None, "invalid sha256 hash")
    return value.removeprefix("sha256:")


def is_utc(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False
