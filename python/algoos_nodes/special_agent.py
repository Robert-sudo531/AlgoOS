"""Special judge agent: checker execution plus alive-aware result routing."""

from __future__ import annotations

import asyncio

from algoos_control.config import NodeConfig, Settings
from algoos_protocol import loads

from .agent_base import NodeAgentBase
from .messages import fallback, special_result

MAX_CASE_BYTES = 2 << 20
MAX_CHECKER_BYTES = 16 << 20


class SpecialAgent(NodeAgentBase):
    role = "special"

    def __init__(self, config: NodeConfig, settings: Settings | None = None):
        super().__init__(config, settings)

    async def _handle(self, message) -> None:
        task = loads(message.data, self.policy)
        if task["type"] != "special_task":
            raise ValueError("special agent received a non-special message")
        reply_to = task["reply_to"]
        try:
            input_file = await asyncio.to_thread(self.cache.fetch, self.store, "input",
                                                 task["input_hash"], MAX_CASE_BYTES)
            output_file = await asyncio.to_thread(self.cache.fetch, self.store, "output",
                                                  task["out_hash"], MAX_CASE_BYTES)
            answer_file = await asyncio.to_thread(self.cache.fetch, self.store, "answer",
                                                  task["ans_hash"], MAX_CASE_BYTES)
            checker = await asyncio.to_thread(self.cache.fetch, self.store, "checker",
                                              task["checker_hash"], MAX_CHECKER_BYTES)
            paths = {"input": str(input_file), "output": str(output_file),
                     "answer": str(answer_file), "checker": str(checker)}
            response = await asyncio.to_thread(
                self.native.call, {"cmd": "judge", "task": task, "paths": paths})
            verdict = response.get("verdict", "SE")
            reason = response.get("reason", "SYSTEM_ERROR")
            detail = response.get("detail", "")
            stderr_tail = response.get("stderr_tail", "")
            cpu_ms = response.get("cpu_ms")
            wall_ms = response.get("wall_ms")
        except Exception as error:
            verdict, reason, detail, stderr_tail = "SE", "SYSTEM_ERROR", str(error), ""
            cpu_ms = wall_ms = None
        target = reply_to["eval_node_id"]
        alive = await self.redis.exists(f"algoos:heartbeat:{target}") if self.redis else 0
        result = special_result(
            task, target_node=target, session_id=reply_to["session_id"], verdict=verdict,
            reason=reason, detail=detail, stderr_tail=stderr_tail, cpu_ms=cpu_ms,
            wall_ms=wall_ms)
        try:
            if not alive:
                raise ConnectionError("target eval node is not alive")
            subject = self.policy["transport"]["special_result_subject_template"].format(
                target_eval_node=target)
            await self.publish(subject, result)
        except Exception as error:
            await self.publish(
                self.policy["transport"]["fallback_subject"],
                fallback(task, reply_to, verdict=verdict, reason=reason,
                         fallback_reason="target_eval_node_unreachable", detail=str(error)))
        await message.ack()

    async def run(self) -> None:
        await self.setup()
        self.install_signals()
        heartbeat = asyncio.create_task(self.heartbeat_loop())
        try:
            # A shared durable lets several special workers pull from one queue.
            await self._consume(self.policy["transport"]["special_task_subject"],
                                "special-workers", self._handle)
        finally:
            self.stop.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self.teardown()
