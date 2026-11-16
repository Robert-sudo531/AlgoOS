"""Evaluation node agent: NATS -> local cache -> native runner -> results."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from pathlib import Path

from algoos_control.broker import wait_for_message
from algoos_control.config import NodeConfig, Settings
from algoos_protocol import loads

from .agent_base import NodeAgentBase
from .messages import cancelled_result, eval_result, special_task

MAX_BIN_BYTES = 16 << 20
MAX_CASE_BYTES = 2 << 20


class EvalAgent(NodeAgentBase):
    role = "eval"

    def __init__(self, config: NodeConfig, settings: Settings | None = None):
        super().__init__(config, settings)

    async def _handle(self, message) -> None:
        task = loads(message.data, self.policy)
        if task["type"] != "eval_task":
            raise ValueError("eval agent received a non-eval message")
        if task["target_node_id"] != self.node_id:
            raise ValueError("eval task is routed to another node")
        if self.check_cancelled(task["file_id"], task["judge_id"], task["sequence"]):
            await self.publish(self.policy["transport"]["eval_result_subject"],
                               cancelled_result(task, self.node_id,
                                                self.cancel_reason(task["file_id"],
                                                                   task["judge_id"],
                                                                   task["sequence"])))
            await message.ack()
            return
        if task["judge_mode"] == "special":
            await self._run_special(task)
        else:
            await self._run_normal(task)
        await message.ack()

    async def _paths(self, task: dict) -> tuple[dict[str, str], bool]:
        cached = self.cache.has("bin", task["bin_hash"]) and self.cache.has(
            "input", task.get("input_hash") or "")
        binary = await asyncio.to_thread(self.cache.fetch, self.store, "bin",
                                         task["bin_hash"], MAX_BIN_BYTES)
        input_file = await asyncio.to_thread(self.cache.fetch, self.store, "input",
                                             task.get("input_hash"), MAX_CASE_BYTES)
        paths = {"binary": str(binary), "input": str(input_file)}
        if task.get("answer_hash"):
            cached = cached and self.cache.has("answer", task["answer_hash"])
            answer = await asyncio.to_thread(self.cache.fetch, self.store, "answer",
                                             task["answer_hash"], MAX_CASE_BYTES)
            paths["answer"] = str(answer)
        return paths, cached

    async def _run_normal(self, task: dict) -> None:
        try:
            paths, cache_hit = await self._paths(task)
            response = await asyncio.to_thread(
                self.native.call, {"cmd": "run", "task": task, "paths": paths})
            if "type" not in response:
                raise RuntimeError(response.get("error", "native runner failed"))
            response["cache_hit"] = cache_hit
            await self.publish(self.policy["transport"]["eval_result_subject"], response)
        except Exception as error:
            await self.publish(self.policy["transport"]["eval_result_subject"],
                               eval_result(task, self.node_id, verdict="SE",
                                           reason="SYSTEM_ERROR", detail=str(error)))
        finally:
            # A response from run_raw can leave a temporary native work dir;
            # normal runs are cleaned inside the native service.
            pass

    async def _run_special(self, task: dict) -> None:
        if not task.get("answer_hash") or not task.get("checker_hash"):
            await self.publish(self.policy["transport"]["eval_result_subject"],
                               eval_result(task, self.node_id, verdict="SE",
                                           reason="SYSTEM_ERROR",
                                           detail="special task lacks object hashes"))
            return
        try:
            paths, _cache_hit = await self._paths(task)
            response = await asyncio.to_thread(
                self.native.call, {"cmd": "run_raw", "task": task, "paths": paths})
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "native raw run failed"))
        except Exception as error:
            await self.publish(self.policy["transport"]["eval_result_subject"],
                               eval_result(task, self.node_id, verdict="SE",
                                           reason="SYSTEM_ERROR", detail=str(error)))
            return
        work_dir = response.get("work_dir")
        try:
            kind = response.get("kind")
            if kind == "cancelled":
                await self.publish(self.policy["transport"]["eval_result_subject"],
                                   cancelled_result(task, self.node_id,
                                                    response.get("reason", "SHORT_CIRCUITED")))
                return
            if kind != "compare":
                await self.publish(
                    self.policy["transport"]["eval_result_subject"],
                    eval_result(task, self.node_id, verdict=response["verdict"],
                                reason=response["reason"], cpu_ms=response.get("cpu_ms"),
                                wall_ms=response.get("wall_ms"),
                                mem_bytes=response.get("mem_bytes"),
                                memory_source=response.get("memory_source"),
                                out_bytes=response.get("out_bytes"),
                                exit_code=response.get("exit_code"),
                                signal=response.get("signal"),
                                wall_timeout=response.get("wall_timeout", False),
                                stderr_tail=response.get("stderr_tail", ""),
                                detail=response.get("detail", "")))
                return
            out_path = Path(response["out_path"])
            output = out_path.read_bytes()
            out_hash = await asyncio.to_thread(
                self.store.put_output, task["file_id"], task["test_id"],
                task["attempt"], output)
            reply_topic = self.policy["transport"]["special_result_subject_template"].format(
                target_eval_node=self.node_id)
            message = special_task(
                task, out_hash=out_hash, ans_hash=task["answer_hash"],
                checker_hash=task["checker_hash"], input_hash=task.get("input_hash"),
                reply_topic=reply_topic,
                fallback_topic=self.policy["transport"]["fallback_subject"],
                session_id=self.session,
                limits=self.policy["special"]["checker_limits"],
                enqueue_seq=task["enqueue_seq"],
                dispatch_generation=task.get("dispatch_generation", 1))
            await self.publish(self.policy["transport"]["special_task_subject"], message)
        finally:
            if work_dir:
                shutil.rmtree(work_dir, ignore_errors=True)

    async def on_special_result(self, message) -> None:
        try:
            result = loads(message.data, self.policy)
            if result["type"] != "special_result":
                raise ValueError("expected special_result")
            if result["target_eval_node"] != self.node_id:
                await message.ack()
                return
            # Do not require the current agent session: an agent restart changes
            # self.session, but the per-node durable may replay a special result
            # produced by the previous session.  judge_id remains the idempotency
            # key, so accepting the old session is safe.
            short_circuited = self.check_cancelled(result["file_id"], result["judge_id"],
                                                   result["sequence"])
            task_stub = {
                "file_id": result["file_id"], "problem_id": result["problem_id"],
                "test_id": result["test_id"], "judge_id": result["judge_id"],
                "sequence": result["sequence"], "attempt": result["attempt"],
            }
            payload = eval_result(
                task_stub, self.node_id, verdict=result["verdict"], reason=result["reason"],
                cpu_ms=result.get("cpu_ms"), wall_ms=result.get("wall_ms"),
                mem_bytes=result.get("mem_bytes"),
                memory_source=result.get("memory_source"),
                out_bytes=None, exit_code=None, signal=None, wall_timeout=False,
                short_circuited=short_circuited,
                stderr_tail=result.get("stderr_tail", ""),
                detail=result.get("detail", ""), source="special",
                cache_hit=bool(result.get("cache_hit", False)))
            await self.publish(self.policy["transport"]["eval_result_subject"], payload)
            await message.ack()
        except Exception as error:
            print(f"[{self.node_id}] special relay failed: {error}", flush=True)
            with contextlib.suppress(Exception):
                await message.nak()

    async def _consume_direct(self, subject: str, durable: str, handler,
                              ack_wait: int | None = None) -> None:
        """Pull control messages without consuming a runner slot or semaphore."""
        subscription = await self.broker.pull(
            subject, durable, ack_wait=ack_wait or self.settings.control_ack_wait)
        while not self.stop.is_set():
            message = await wait_for_message(subscription, 1.0)
            if message is None:
                continue
            try:
                await handler(message)
            except Exception as error:  # keep the loop alive; redelivery is safe
                print(f"[{self.node_id}] control message failed: {error}", flush=True)
                with contextlib.suppress(Exception):
                    await message.nak()

    async def run(self) -> None:
        await self.setup()
        self.install_signals()
        transport = self.policy["transport"]
        cancel_subject = transport["cancel_subject"]
        special_subject = transport["special_result_subject_template"].format(
            target_eval_node=self.node_id)
        # Durable consumers make cancel broadcasts and special relays survive
        # a short node/agent restart instead of silently dropping the message.
        control_tasks = [
            asyncio.create_task(self._consume_direct(
                cancel_subject, f"cancel-{self.node_id}", self.on_cancel)),
            asyncio.create_task(self._consume_direct(
                special_subject, f"special-result-{self.node_id}", self.on_special_result)),
        ]
        heartbeat = asyncio.create_task(self.heartbeat_loop())
        subject = transport["eval_task_subject_template"].format(
            target_node_id=self.node_id)
        try:
            await self._consume(subject, f"eval-{self.node_id}", self._handle,
                                ack_wait=self.settings.eval_ack_wait)
        finally:
            self.stop.set()
            for task in (*control_tasks, heartbeat):
                task.cancel()
            await asyncio.gather(*control_tasks, heartbeat, return_exceptions=True)
            await self.teardown()
