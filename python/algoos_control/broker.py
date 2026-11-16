"""NATS JetStream wrapper shared by control and node agents."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import nats
from algoos_protocol import dumps
from nats.js.api import AckPolicy, ConsumerConfig, StorageType, StreamConfig

from .config import Settings

MAX_MESSAGE_BYTES = 8 << 20


class Broker:
    def __init__(self, settings: Settings, policy: dict):
        self.settings = settings
        self.policy = policy
        self.transport = policy["transport"]
        self.client: nats.NATS | None = None
        self.js: Any = None

    async def connect(self) -> Broker:
        options: dict[str, Any] = {"allow_reconnect": True, "connect_timeout": 2,
                                   "max_reconnect_attempts": 60}
        if self.settings.nats_token:
            options["token"] = self.settings.nats_token
        self.client = await nats.connect(self.settings.nats_url, **options)
        self.js = self.client.jetstream(timeout=5)
        if self.settings.ensure_stream:
            await self._ensure_stream()
        return self

    async def _ensure_stream(self) -> None:
        name = self.transport["stream_name"]
        subjects = [
            self.transport["compile_task_subject"],
            self.transport["compile_result_subject"],
            "algoos.eval.task.>",
            self.transport["eval_result_subject"],
            self.transport["special_task_subject"],
            "algoos.special.result.>",
            self.transport["cancel_subject"],
            self.transport["fallback_subject"],
            self.transport["heartbeat_subject"],
        ]
        try:
            info = await self.js.stream_info(name)
            current = set(info.config.subjects)
            if not set(subjects).issubset(current):
                await self.js.update_stream(config=StreamConfig(
                    name=name, subjects=sorted(current | set(subjects)),
                    storage=StorageType.FILE, max_msgs=200_000,
                    max_bytes=8 << 30, duplicate_window=120))
        except Exception:
            await self.js.add_stream(config=StreamConfig(
                name=name, subjects=subjects, storage=StorageType.FILE,
                max_msgs=200_000, max_bytes=8 << 30, duplicate_window=120))

    async def publish(self, subject: str, message: dict, msg_id: str | None = None) -> None:
        payload = dumps(message, self.policy).encode("utf-8")
        if len(payload) > MAX_MESSAGE_BYTES:
            raise ValueError("message exceeds the 8 MiB transport cap")
        headers = {"Nats-Msg-Id": msg_id or message["msg_id"]}
        await self.js.publish(subject, payload, headers=headers, timeout=5)

    async def pull(self, subject: str, durable: str, *, ack_wait: int = 60):
        return await self.js.pull_subscribe(
            subject, durable=durable, stream=self.transport["stream_name"],
            config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, ack_wait=ack_wait,
                                  max_deliver=20))

    async def fetch(self, subscription, count: int = 1, timeout: float = 2.0):
        return await subscription.fetch(count, timeout=timeout)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None


@asynccontextmanager
async def connected(settings: Settings, policy: dict) -> AsyncIterator[Broker]:
    broker = Broker(settings, policy)
    await broker.connect()
    try:
        yield broker
    finally:
        await broker.close()


async def wait_for_message(subscription, timeout: float = 2.0):
    """Fetch one message, returning None on an empty poll instead of raising."""
    try:
        messages = await subscription.fetch(1, timeout=timeout)
        return messages[0] if messages else None
    except TimeoutError:
        return None
    except Exception as error:  # nats-py raises a bare Exception on timeout
        if "timeout" in str(error).lower() or "no messages" in str(error).lower():
            return None
        raise
