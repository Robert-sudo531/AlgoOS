"""Pydantic models for the AlgoOS v1 wire protocol.

The models deliberately allow unknown fields (``extra="allow"``) so that v1
messages remain forward compatible: a new node may add fields without breaking
older consumers, and unknown fields survive a parse/dump round trip.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

HASH_PATTERN = r"^sha256:[0-9a-f]{64}$"
NODE_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
UTC_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"

Hash = Annotated[str, StringConstraints(pattern=HASH_PATTERN)]
NodeId = Annotated[str, StringConstraints(pattern=NODE_PATTERN)]
UtcTimestamp = Annotated[str, StringConstraints(pattern=UTC_PATTERN)]


class Verdict(StrEnum):
    AC = "AC"
    WA = "WA"
    TLE = "TLE"
    MLE = "MLE"
    RE = "RE"
    WR = "WR"
    CE = "CE"
    SE = "SE"
    CANCELLED = "CANCELLED"


class Reason(StrEnum):
    NONE = "NONE"
    ANSWER_MISMATCH = "ANSWER_MISMATCH"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    WALL_TIMEOUT = "WALL_TIMEOUT"
    CPU_TIMEOUT = "CPU_TIMEOUT"
    OOM_KILL = "OOM_KILL"
    MEM_PEAK_OVER = "MEM_PEAK_OVER"
    CRASH_SIGNAL = "CRASH_SIGNAL"
    NONZERO_EXIT = "NONZERO_EXIT"
    SHORT_CIRCUITED = "SHORT_CIRCUITED"
    USER_CANCELLED = "USER_CANCELLED"
    COMPILE_ERROR = "COMPILE_ERROR"
    COMPILE_TIMEOUT = "COMPILE_TIMEOUT"
    COMPILE_LIMIT = "COMPILE_LIMIT"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    SPECIAL_WA = "SPECIAL_WA"
    NO_NODE = "NO_NODE"


class SubmissionStatus(StrEnum):
    QUEUED = "QUEUED"
    COMPILING = "COMPILING"
    COMPILED = "COMPILED"
    EVALUATING = "EVALUATING"
    PENDING_SPECIAL = "PENDING_SPECIAL"
    SHORT_CIRCUITED = "SHORT_CIRCUITED"
    CANCELLED = "CANCELLED"
    FINAL = "FINAL"
    CE = "CE"


class JudgeMode(StrEnum):
    normal = "normal"
    special = "special"


class CompareMode(StrEnum):
    common = "common"
    special = "special"


class EvaluationMode(StrEnum):
    all_cases = "all_cases"
    ordered_short_circuit = "ordered_short_circuit"


class Source(StrEnum):
    compiler = "compiler"
    runner = "runner"
    special = "special"
    fallback = "fallback"


class NodeRole(StrEnum):
    control = "control"
    compile = "compile"
    eval = "eval"
    special = "special"


class MessageBase(BaseModel):
    model_config = ConfigDict(extra="allow", use_enum_values=True)

    ver: int = 1
    msg_id: str = Field(min_length=1, max_length=256)
    type: str
    file_id: str = Field(min_length=1, max_length=128)
    problem_id: str | None = Field(default=None, max_length=64)
    correlation_id: str = Field(min_length=1, max_length=128)
    emitted_at: UtcTimestamp
    attempt: int = Field(default=1, ge=1, le=1_000_000)

    @field_validator("file_id")
    @classmethod
    def _no_colon(cls, value: str) -> str:
        if ":" in value:
            raise ValueError("file_id must not contain ':'")
        return value

    @model_validator(mode="after")
    def _correlation(self) -> MessageBase:
        if self.correlation_id != self.file_id:
            raise ValueError("correlation_id must equal file_id")
        return self


class Limits(BaseModel):
    model_config = ConfigDict(extra="allow")

    cpu_ms: int = Field(gt=0, le=3_600_000)
    wall_ms: int = Field(gt=0, le=3_600_000)
    mem_bytes: int = Field(gt=0, le=8 << 30)
    out_bytes: int = Field(ge=0, le=256 << 20)


class ReplyTo(BaseModel):
    model_config = ConfigDict(extra="allow")

    eval_node_id: NodeId
    session_id: str = Field(min_length=1, max_length=128)
    reply_topic: str = Field(min_length=1, max_length=256)
    deadline_ms: int = Field(gt=0, description="absolute Unix epoch milliseconds")


class CompileTask(MessageBase):
    type: Literal["compile_task"]
    problem_id: str = Field(min_length=1, max_length=64)
    source_hash: Hash
    test_bundle_hash: Hash
    compiler_profile: str = Field(min_length=1, max_length=64)
    limits: Limits
    enqueue_seq: int = Field(ge=1)


class EvalTask(MessageBase):
    type: Literal["eval_task"]
    problem_id: str = Field(min_length=1, max_length=64)
    test_id: str = Field(min_length=1, max_length=64)
    judge_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=1)
    enqueue_seq: int = Field(ge=1)
    bin_hash: Hash
    test_bundle_hash: Hash
    answer_hash: Hash | None = None
    checker_hash: Hash | None = None
    judge_mode: JudgeMode = JudgeMode.normal
    compare_mode: CompareMode = CompareMode.common
    evaluation_mode: EvaluationMode = EvaluationMode.all_cases
    target_node_id: NodeId
    dispatch_generation: int = Field(default=1, ge=1)
    limits: Limits

    @field_validator("test_id")
    @classmethod
    def _test_no_colon(cls, value: str) -> str:
        if ":" in value:
            raise ValueError("test_id must not contain ':'")
        return value

    @model_validator(mode="after")
    def _judge_id(self) -> EvalTask:
        expected = f"{self.file_id}:{self.test_id}:{self.attempt}"
        if self.judge_id != expected:
            raise ValueError(f"judge_id must be {expected}")
        return self


class CompileResult(MessageBase):
    type: Literal["eval_result"]
    stage: Literal["compile"] = "compile"
    status: Literal["COMPILED", "FINAL"]
    node_id: NodeId
    source: Literal["compiler"] = "compiler"
    problem_id: str = Field(min_length=1, max_length=64)
    test_bundle_hash: Hash
    compiler_profile: str = Field(min_length=1, max_length=64)
    enqueue_seq: int = Field(ge=1)
    bin_hash: Hash | None = None
    build_key: Hash | None = None
    compiler_version: str | None = None
    verdict: Verdict | None = None
    reason: Reason = Reason.NONE
    test_id: None = None
    sequence: None = None
    judge_id: None = None
    exit_code: None = None
    signal: None = None
    cpu_ms: int | None = None
    wall_ms: int | None = None
    mem_bytes: int | None = None
    memory_source: str | None = None
    out_bytes: int | None = None
    wall_timeout: bool = False
    short_circuited: bool = False
    stderr_tail: str = ""
    detail: str = ""

    @model_validator(mode="after")
    def _compile_outcome(self) -> CompileResult:
        if self.status == "COMPILED":
            if self.bin_hash is None or self.build_key is None or self.compiler_version is None:
                raise ValueError("COMPILED requires bin_hash, build_key and compiler_version")
            if self.verdict is not None or self.reason != Reason.NONE:
                raise ValueError("COMPILED must not carry a verdict")
        else:
            if self.verdict not in {Verdict.CE, Verdict.SE}:
                raise ValueError("a failed compile result must be CE or SE")
        return self


class EvalResult(MessageBase):
    type: Literal["eval_result"]
    stage: Literal["eval"] = "eval"
    status: Literal["FINAL"] = "FINAL"
    problem_id: str = Field(min_length=1, max_length=64)
    test_id: str = Field(min_length=1, max_length=64)
    judge_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=1)
    node_id: NodeId
    source: Literal["runner", "special", "fallback"]
    verdict: Verdict
    reason: Reason
    cpu_ms: int | None = Field(default=None, ge=0)
    wall_ms: int | None = Field(default=None, ge=0)
    mem_bytes: int | None = Field(default=None, ge=0)
    memory_source: str | None = None
    out_bytes: int | None = Field(default=None, ge=0)
    exit_code: int | None = None
    signal: int | None = None
    wall_timeout: bool = False
    short_circuited: bool = False
    cache_hit: bool = False
    stderr_tail: str = ""
    detail: str = ""

    @model_validator(mode="after")
    def _eval_identity(self) -> EvalResult:
        if ":" in self.test_id:
            raise ValueError("test_id must not contain ':'")
        expected = f"{self.file_id}:{self.test_id}:{self.attempt}"
        if self.judge_id != expected:
            raise ValueError(f"judge_id must be {expected}")
        if (self.mem_bytes is None) != (self.memory_source is None):
            raise ValueError("mem_bytes and memory_source must be provided together")
        if self.signal not in (None, 0) and self.exit_code is not None:
            raise ValueError("a signalled process must have exit_code=null")
        if self.wall_timeout and self.reason not in {
            Reason.WALL_TIMEOUT,
            Reason.OOM_KILL,
            Reason.SYSTEM_ERROR,
        }:
            raise ValueError("wall_timeout evidence conflicts with reason")
        return self


class SpecialTask(MessageBase):
    type: Literal["special_task"]
    problem_id: str = Field(min_length=1, max_length=64)
    test_id: str = Field(min_length=1, max_length=64)
    judge_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=1)
    out_hash: Hash
    ans_hash: Hash
    checker_hash: Hash
    input_hash: Hash | None = None
    test_bundle_hash: Hash | None = None
    limits: Limits
    reply_to: ReplyTo
    fallback_topic: str = Field(min_length=1, max_length=256)
    enqueue_seq: int = Field(ge=1)

    @model_validator(mode="after")
    def _special_identity(self) -> SpecialTask:
        expected = f"{self.file_id}:{self.test_id}:{self.attempt}"
        if self.judge_id != expected:
            raise ValueError(f"judge_id must be {expected}")
        return self


class SpecialResult(MessageBase):
    type: Literal["special_result"]
    problem_id: str = Field(min_length=1, max_length=64)
    test_id: str = Field(min_length=1, max_length=64)
    judge_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=1)
    target_eval_node: NodeId
    session_id: str = Field(min_length=1, max_length=128)
    verdict: Literal["AC", "WA", "SE"]
    reason: Reason
    cpu_ms: int | None = Field(default=None, ge=0)
    wall_ms: int | None = Field(default=None, ge=0)
    mem_bytes: int | None = Field(default=None, ge=0)
    memory_source: str | None = None
    source: Literal["special"] = "special"
    short_circuited: bool = False
    detail: str = ""
    stderr_tail: str = ""

    @model_validator(mode="after")
    def _special_identity(self) -> SpecialResult:
        expected = f"{self.file_id}:{self.test_id}:{self.attempt}"
        if self.judge_id != expected:
            raise ValueError(f"judge_id must be {expected}")
        if (self.mem_bytes is None) != (self.memory_source is None):
            raise ValueError("mem_bytes and memory_source must be provided together")
        return self


class Cancel(MessageBase):
    type: Literal["cancel"]
    problem_id: str | None = Field(default=None, max_length=64)
    judge_id: str | None = Field(default=None, max_length=256)
    scope: Literal["submission", "case"]
    reason: Reason
    origin: str = Field(min_length=1, max_length=64)
    sequence_boundary: int | None = Field(default=None, ge=1)
    cancel_generation: int = Field(ge=1)
    ts: UtcTimestamp


class Fallback(MessageBase):
    type: Literal["fallback"]
    problem_id: str = Field(min_length=1, max_length=64)
    test_id: str = Field(min_length=1, max_length=64)
    judge_id: str = Field(min_length=1, max_length=256)
    sequence: int = Field(ge=1)
    reply_to: ReplyTo
    verdict: Literal["AC", "WA", "SE"]
    reason: Reason
    source: Literal["special"] = "special"
    fallback_reason: str = Field(min_length=1, max_length=128)
    detail: str = ""
    stderr_tail: str = ""

    @model_validator(mode="after")
    def _fallback_identity(self) -> Fallback:
        expected = f"{self.file_id}:{self.test_id}:{self.attempt}"
        if self.judge_id != expected:
            raise ValueError(f"judge_id must be {expected}")
        return self


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="allow")

    node_id: NodeId
    role: NodeRole
    version: str
    slots_total: int = Field(ge=1, le=1024)
    slots_used: int = Field(ge=0, le=1024)
    load_score: float = Field(ge=0)
    cached_hashes: list[str] = Field(default_factory=list, max_length=4096)
    sent_at_ms: int = Field(gt=0)
    endpoint: str | None = None


MESSAGE_TYPES: dict[str, type[MessageBase]] = {
    "compile_task": CompileTask,
    "eval_task": EvalTask,
    "eval_result": EvalResult,
    "special_task": SpecialTask,
    "special_result": SpecialResult,
    "cancel": Cancel,
    "fallback": Fallback,
}


def message_model(payload: dict[str, Any]) -> type[MessageBase]:
    kind = payload.get("type")
    if kind == "eval_result":
        stage = payload.get("stage")
        if stage == "compile":
            return CompileResult
        if stage == "eval":
            return EvalResult
        raise ValueError("eval_result requires stage=compile|eval")
    if kind not in MESSAGE_TYPES:
        raise ValueError(f"unsupported message type: {kind!r}")
    return MESSAGE_TYPES[kind]
