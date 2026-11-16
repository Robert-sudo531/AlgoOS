"""Shared NATS/Redis/cache plumbing for compile, eval and special agents."""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
from contextlib import suppress
from typing import Any
from uuid import uuid4

import redis.asyncio as redis
from algoos_control.broker import Broker
from algoos_control.config import NodeConfig, Settings, load_policy
from algoos_control.storage import ObjectStore

from algoos_nodes.cache import LocalCache
from algoos_nodes.native_client import NativeClient, NodeHealth

HEARTBEAT_INTERVAL_S = 2.0
HEARTBEAT_TTL_S = 6


class NodeAgentBase:
    role: str = "eval"

    def __init__(self, config: NodeConfig, settings: Settings | None = None):
        self.config = config
        self.settings = settings or Settings()
        self.policy = load_policy(self.settings)
        self.store = ObjectStore(self.settings)
        self.cache = LocalCache(config.cache_root, config.cache_max_bytes)
        self.native = NativeClient(config.native_socket)
        self.broker = Broker(self.settings, self.policy)
        self.redis: redis.Redis | None = None
        self.health = NodeHealth(config.max_parallel)
        self.semaphore = asyncio.Semaphore(config.max_parallel)
        self.stop = asyncio.Event()
        # file_id -> {"all": bool, "boundary": int|None, "judges": set[str],
        #             "generation": int, "reason": str}
        self.cancels: dict[str, dict[str, Any]] = {}
        self.session = f"{config.node_id}-{uuid4().hex[:8]}"
        self._tasks: set[asyncio.Task] = set()

    @property
    def node_id(self) -> str:
        return self.config.node_id

    async def setup(self) -> None:
        self.store.ensure_bucket()
        await self.broker.connect()
        self.redis = redis.from_url(self.settings.redis_url, decode_responses=True)

    async def teardown(self) -> None:
        await self.broker.close()
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None

    # ---- cancellation ------------------------------------------------
    def check_cancelled(self, file_id: str, judge_id: str, sequence: int) -> bool:
        state = self.cancels.get(file_id)
        if not state:
            return False
        if state.get("all"):
            return True
        boundary = state.get("boundary")
        if boundary is not None and sequence > boundary:
            return True
        return judge_id in state.get("judges", set())

    def cancel_reason(self, file_id: str, judge_id: str, sequence: int) -> str:
        state = self.cancels.get(file_id, {})
        if state.get("all") or (state.get("boundary") is not None
                                and sequence > state["boundary"]):
            return state.get("reason", "SHORT_CIRCUITED")
        if judge_id in state.get("judges", set()):
            return state.get("reason", "USER_CANCELLED")
        return "USER_CANCELLED"

    async def on_cancel(self, message) -> None:
        try:
            from algoos_protocol import loads

            cancel = loads(message.data, self.policy)
            file_id = cancel["file_id"]
            state = self.cancels.setdefault(file_id, {
                "all": False, "boundary": None, "judges": set(), "generation": 0,
                "reason": "SHORT_CIRCUITED"})
            if cancel["cancel_generation"] < state["generation"]:
                await message.ack()
                return
            state["generation"] = cancel["cancel_generation"]
            state["reason"] = cancel["reason"]
            if cancel["scope"] == "case":
                state["judges"].add(cancel["judge_id"])
            elif cancel["sequence_boundary"] is not None:
                boundary = int(cancel["sequence_boundary"])
                state["boundary"] = boundary if state["boundary"] is None else min(
                    boundary, state["boundary"])
            else:
                state["all"] = True
            if self.role == "eval":
                try:
                    self.native.call({"cmd": "cancel", "file_id": file_id,
                                      "judge_id": cancel.get("judge_id"),
                                      "sequence_boundary": cancel.get("sequence_boundary"),
                                      "reason": cancel["reason"]})
                except Exception as error:
                    print(f"[{self.node_id}] native cancel failed: {error}", flush=True)
            await message.ack()
        except Exception as error:
            print(f"[{self.node_id}] cancel handling failed: {error}", flush=True)
            with contextlib.suppress(Exception):
                await message.ack()

    # ---- heartbeat ---------------------------------------------------
    async def heartbeat_loop(self) -> None:
        assert self.redis is not None
        transport = self.policy["transport"]
        while not self.stop.is_set():
            payload = {
                "node_id": self.node_id,
                "role": self.role,
                "version": "1.0.0",
                "slots_total": self.config.max_parallel,
                "slots_used": self.health.running,
                "load_score": self.health.load_ms,
                "cached_hashes": self.cache.hashes(),
                "sent_at_ms": int(asyncio.get_running_loop().time() * 1000),
                "endpoint": self.native.socket_path,
            }
            try:
                await self.redis.setex(f"algoos:heartbeat:{self.node_id}", HEARTBEAT_TTL_S,
                                       json.dumps(payload))
                assert self.broker.client is not None
                await self.broker.client.publish(
                    transport["heartbeat_subject"],
                    json.dumps(payload, separators=(",", ":")).encode("utf-8"))
            except Exception as error:
                print(f"[{self.node_id}] heartbeat failed: {error}", flush=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stop.wait(), timeout=HEARTBEAT_INTERVAL_S)

    # ---- helpers -----------------------------------------------------
    async def publish(self, subject: str, message: dict) -> None:
        await self.broker.publish(subject, message, message["msg_id"])

    def install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):  # pragma: no cover
                loop.add_signal_handler(signum, self.stop.set)

    async def run(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    async def _consume(self, subject: str, durable: str, handler,
                       ack_wait: int | None = None) -> None:
        subscription = await self.broker.pull(
            subject, durable, ack_wait=ack_wait or self.settings.eval_ack_wait)
        while not self.stop.is_set():
            from algoos_control.broker import wait_for_message

            message = await wait_for_message(subscription, 1.0)
            if message is None:
                continue
            task = asyncio.create_task(self._run_handler(message, handler))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            await asyncio.sleep(0)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run_handler(self, message, handler) -> None:
        async with self.semaphore:
            estimate = 1500.0
            self.health.start(estimate)
            try:
                await handler(message)
            except Exception as error:
                print(f"[{self.node_id}] message failed: {error}", flush=True)
                with contextlib.suppress(Exception):
                    await message.nak()
            finally:
                self.health.finish(estimate)
