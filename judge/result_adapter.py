"""N3.1 第一小步：把旧 C++ 服务的真实 AC 响应转换为 v1 结果消息。

本模块不接收分布式任务、不解析对象哈希，也不猜测异常结果的 reason。
"""

from datetime import datetime, timezone
from uuid import uuid4

from protocol.codec_v1 import ContractError, normalize


def adapt_ac_result(request, response, *, sequence, attempt, node_id, policy):
    """保留真实测量值；未采集的墙钟耗时明确留空。"""
    if type(response.get("ver")) is not int or response["ver"] != 1:
        raise ContractError("原生响应版本错误")
    for key in ("file_id", "problem_id", "test_id"):
        if key not in request or response.get(key) != request[key]:
            raise ContractError(f"原生响应身份不一致：{key}")
    if response.get("verdict") != "AC":
        raise NotImplementedError("当前小步只适配 AC；其他判定在后续接入")
    if (type(response.get("exit_code")) is not int or response["exit_code"] != 0
            or type(response.get("signal")) is not int or response["signal"] != 0
            or response.get("wall_timeout") is not False or response.get("error") != ""):
        raise ContractError("AC 响应与真实退出/错误状态冲突")
    for key in ("cpu_ms", "mem_kb", "out_bytes"):
        if type(response.get(key)) is not int or response[key] < 0:
            raise ContractError(f"原生测量字段无效：{key}")
    if response["out_bytes"] > request["limits"]["out_bytes"]:
        raise ContractError("AC 响应的输出长度超过限制")

    file_id, test_id = request["file_id"], request["test_id"]
    message = {
        "ver": 1, "type": "eval_result", "status": "FINAL",
        "msg_id": f"result_{uuid4().hex}",
        "correlation_id": file_id,
        "emitted_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "file_id": file_id, "problem_id": request["problem_id"],
        "stage": "eval", "test_id": test_id, "sequence": sequence, "attempt": attempt,
        "judge_id": f"{file_id}:{test_id}:{attempt}", "node_id": node_id,
        "verdict": "AC", "reason": "NONE",
        "cpu_ms": response["cpu_ms"], "wall_ms": None,
        "mem_bytes": response["mem_kb"] * 1024, "memory_source": "rss_peak",
        "out_bytes": response["out_bytes"],
        "exit_code": response["exit_code"], "signal": response["signal"],
        "wall_timeout": response["wall_timeout"], "short_circuited": False,
        "source": "runner", "stderr_tail": response["stderr_tail"], "detail": "",
    }
    return normalize(message, policy)


# 2026-11-11 追加：WA 内容错误适配；上方 AC 入口完整保留。
def adapt_wa_mismatch_result(request, response, *, sequence, attempt, node_id, policy):
    """依据实际文件区分答案不一致；输出超限等路径留待后续适配。"""
    from pathlib import Path

    from judge.problem import trim_common

    if type(response.get("ver")) is not int or response["ver"] != 1:
        raise ContractError("原生响应版本错误")
    for key in ("file_id", "problem_id", "test_id"):
        if key not in request or response.get(key) != request[key]:
            raise ContractError(f"原生响应身份不一致：{key}")
    if response.get("verdict") != "WA" or request.get("compare_mode") != "common":
        raise NotImplementedError("本入口只适配 common 模式的 WA 内容错误")
    if (type(response.get("exit_code")) is not int or response["exit_code"] != 0
            or type(response.get("signal")) is not int or response["signal"] != 0
            or response.get("wall_timeout") is not False or response.get("error") != ""):
        raise ContractError("内容错误适配要求程序正常退出、无信号或系统错误")
    for key in ("cpu_ms", "mem_kb", "out_bytes"):
        if type(response.get(key)) is not int or response[key] < 0:
            raise ContractError(f"原生测量字段无效：{key}")
    limit = request["limits"]["out_bytes"]
    if type(limit) is not int or limit < 0:
        raise ContractError("输出限制必须是非负整数")
    runtime = policy["runtime"]
    fsize_cap = min(runtime["fsize_max_bytes"], limit + runtime["fsize_slack_bytes"])

    def read_bounded(path, maximum):
        with Path(path).open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ContractError(f"文件超过本次读取上限：{path}")
        return data

    # 对齐旧原生服务的答案读取上限；输出按本次配置有界读取。
    expected = trim_common(read_bounded(request["answer_path"], 64 << 20))
    if len(expected) != limit:
        raise ContractError("输出限制与归一化答案长度不一致")
    output_path = Path(request["result_dir"]) / (request["test_id"] + ".out")
    actual = trim_common(read_bounded(output_path, fsize_cap))
    if len(actual) > limit:
        raise NotImplementedError("已发现输出超限，不能标为 ANSWER_MISMATCH")
    if len(actual) != response["out_bytes"]:
        raise ContractError("实际归一化输出长度与原生响应不一致")
    if actual == expected:
        raise ContractError("实际输出符合 common 比较规则，与原生 WA 冲突")

    file_id, test_id = request["file_id"], request["test_id"]
    message = {
        "ver": 1, "type": "eval_result", "status": "FINAL",
        "msg_id": f"result_{uuid4().hex}", "correlation_id": file_id,
        "emitted_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "file_id": file_id, "problem_id": request["problem_id"],
        "stage": "eval", "test_id": test_id, "sequence": sequence, "attempt": attempt,
        "judge_id": f"{file_id}:{test_id}:{attempt}", "node_id": node_id,
        "verdict": "WA", "reason": "ANSWER_MISMATCH",
        "cpu_ms": response["cpu_ms"], "wall_ms": None,
        "mem_bytes": response["mem_kb"] * 1024, "memory_source": "rss_peak",
        "out_bytes": response["out_bytes"], "exit_code": response["exit_code"],
        "signal": response["signal"], "wall_timeout": response["wall_timeout"],
        "short_circuited": False, "source": "runner",
        "stderr_tail": response["stderr_tail"], "detail": "",
    }
    return normalize(message, policy)
