"""v1 消息检查与规范化：只补默认 status，保留附加字段，不执行任务。"""

import copy
from datetime import datetime
import json
import math
import re


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def field(obj, key):
    require(type(obj) is dict and key in obj, f"缺少字段：{key}")
    return obj[key]


def string(obj, key, *, empty=False):
    value = field(obj, key)
    require(type(value) is str and (empty or bool(value)), f"{key} 必须是字符串")
    return value


def integer(obj, key, minimum=0, *, nullable=False):
    value = field(obj, key)
    if nullable and value is None:
        return value
    # bool 是 Python int 的子类，所以这里不能用 isinstance(value, int)。
    require(type(value) is int and minimum <= value <= 2**63 - 1,
            f"{key} 必须是范围内的整数")
    return value


def one_of(obj, key, values):
    value = string(obj, key)
    require(value in values, f"{key} 不支持：{value}")
    return value


def hash_field(obj, key):
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", string(obj, key)) is not None,
            f"{key} 哈希格式错误")


def check_tree(value, depth=0):
    """限制 JSON 共同可表示的范围，防止往返时丢失数值或字符串。"""
    require(depth < 64, "JSON 嵌套过深")
    if type(value) is dict:
        for key, item in value.items():
            require(type(key) is str and "\0" not in key, "JSON 键必须是不含 NUL 的字符串")
            check_tree(key, depth + 1)
            check_tree(item, depth + 1)
    elif type(value) is list:
        for item in value:
            check_tree(item, depth + 1)
    elif type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise ContractError("字符串不是有效 Unicode") from error
    elif type(value) is int:
        require(-(2**63) <= value <= 2**63 - 1, "整数超出有符号 64 位范围")
    elif type(value) is float:
        require(math.isfinite(value), "JSON 不接受 NaN/Infinity")
    else:
        require(value is None or type(value) is bool, "不支持的 JSON 类型")


def normalize(message, policy):
    """返回独立的新对象；调用方传入的消息保持原样。"""
    check_tree(message)
    result = copy.deepcopy(message)
    require(type(result) is dict, "消息必须是 JSON 对象")
    require(integer(result, "ver", 1) == policy["ver"], "不支持的版本")
    kind = one_of(result, "type", ("compile_task", "eval_task", "eval_result"))
    for key in ("msg_id", "problem_id", "file_id", "correlation_id", "emitted_at"):
        string(result, key)
    require(":" not in result["file_id"], "file_id 不能含冒号")
    require(result["correlation_id"] == result["file_id"], "correlation_id 不一致")
    timestamp = result["emitted_at"]
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z",
                         timestamp) is not None, "emitted_at 必须是 UTC 时间")
    try:
        datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise ContractError("emitted_at 日期无效") from error
    integer(result, "attempt", 1)

    def node(key):
        require(re.fullmatch(policy["transport"]["node_id_pattern"], string(result, key))
                is not None, f"{key} 格式错误")

    def identity():
        test_id = string(result, "test_id")
        require(":" not in test_id, "test_id 不能含冒号")
        integer(result, "sequence", 1)
        expected = f"{result['file_id']}:{test_id}:{result['attempt']}"
        require(string(result, "judge_id") == expected, "judge_id 不一致")

    def profile():
        return policy["compiler_profiles"][one_of(
            result, "compiler_profile", policy["compiler_profiles"])]

    if kind in ("compile_task", "eval_task"):
        integer(result, "enqueue_seq", 1)
        hash_field(result, "test_bundle_hash")
        limits = field(result, "limits")
        for key in ("cpu_ms", "wall_ms", "mem_bytes", "out_bytes"):
            integer(limits, key, 0 if key == "out_bytes" else 1)
        if kind == "compile_task":
            hash_field(result, "source_hash")
            frozen = profile()["limits"]
            require(all(limits[key] == value for key, value in frozen.items()),
                    "编译限制与配置不一致")
        else:
            identity()
            node("target_node_id")
            hash_field(result, "bin_hash")
            for key, values in policy["modes"].items():
                one_of(result, key, values)
            require(limits["wall_ms"] == limits["cpu_ms"], "v1 墙钟与 CPU 配置必须相等")
        return result

    rules = policy["result"]
    stage = one_of(result, "stage", rules["sources_by_stage"])
    node("node_id")
    require(string(result, "source") == rules["sources_by_stage"][stage], "source 与阶段不一致")
    result.setdefault("status", rules["default_status"])
    status = one_of(result, "status", rules["statuses"])
    for key in ("cpu_ms", "wall_ms", "mem_bytes", "out_bytes", "exit_code", "signal"):
        integer(result, key, nullable=True)
    memory_source = field(result, "memory_source")
    require(memory_source is None or memory_source in rules["memory_sources"], "内存口径不支持")
    require((result["mem_bytes"] is None) == (memory_source is None), "内存数值与口径应同时提供")
    for key in ("wall_timeout", "short_circuited"):
        require(type(field(result, key)) is bool, f"{key} 必须是布尔值")
    require(result["short_circuited"] is False, "v1 不支持短路")
    for key in ("stderr_tail", "detail"):
        string(result, key, empty=True)
    if result["signal"] not in (None, 0):
        require(result["exit_code"] is None, "信号终止时 exit_code 必须为 null")

    if stage == "compile":
        for key in (*rules["compile_identity_fields_null"], "exit_code", "signal"):
            require(field(result, key) is None, f"编译通知的 {key} 必须为 null")
        require(result["wall_timeout"] is False, "编译通知未运行被测程序")
    else:
        identity()
        require(status == "FINAL", "评测结果必须是 FINAL")

    verdict = field(result, "verdict")
    reason = string(result, "reason")
    if status == "COMPILED":
        require(stage == "compile" and verdict is None and reason == "NONE", "编译成功状态不一致")
        for key in rules["compile_success_required_fields"]:
            field(result, key)
        for key in ("bin_hash", "test_bundle_hash"):
            hash_field(result, key)
        if "source_hash" in result:
            hash_field(result, "source_hash")
        profile()
        integer(result, "enqueue_seq", 1)
    else:
        require(type(verdict) is str and verdict in rules["final_verdicts_by_stage"][stage],
                "阶段不支持该判定")
        require(reason in rules["reason_by_verdict"][verdict], "判定与原因不一致")
    if stage == "eval":
        require(reason != "WALL_TIMEOUT" or result["wall_timeout"], "缺少墙钟超时证据")
        require(not result["wall_timeout"] or reason in ("WALL_TIMEOUT", "OOM_KILL", "SYSTEM_ERROR"),
                "墙钟超时标志与原因不一致")
    return result


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"JSON 键重复：{key}")
        result[key] = value
    return result


def loads(payload, policy):
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        message = json.loads(payload, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError) as error:
        raise ContractError(f"JSON 解析失败：{error}") from error
    return normalize(message, policy)


def dumps(message, policy):
    return json.dumps(normalize(message, policy), ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)
