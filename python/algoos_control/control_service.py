"""Long-running control plane: ETL loops around the outbox and results."""

from __future__ import annotations

import asyncio
import json
import signal
from contextlib import suppress
from pathlib import Path

from algoos_protocol import loads

from .aggregator import fallback_to_eval_result, handle_eval_result
from .broker import Broker, wait_for_message
from .compile_flow import handle_compile_result
from .config import Settings, load_policy
from .db import Database
from .heartbeat import mark_stale, record_heartbeat
from .outbox import publish_pending
from .problem import import_problem, load_problem
from .reaper import reap_once
from .scheduler import dispatch_once
from .storage import ObjectStore
from .submissions import upsert_problem


def import_all_problems(database: Database, store: ObjectStore, settings: Settings) -> list[str]:
    imported = []
    for path in sorted(Path(settings.problems_dir).glob("*/problem.json")):
        problem = load_problem(path)
        upsert_problem(database, import_problem(problem, store))
        imported.append(problem.problem_id)
    return imported


class ControlService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.policy = load_policy(self.settings)
        self.db = Database(self.settings)
        self.store = ObjectStore(self.settings)
        self.stop = asyncio.Event()

    async def _consume_compile(self, broker: Broker) -> None:
        subject = self.policy["transport"]["compile_result_subject"]
        subscription = await broker.pull(subject, f"control-compile-{self.settings.control_node_id}")
        while not self.stop.is_set():
            message = await wait_for_message(subscription, 1.0)
            if message is None:
                continue
            try:
                payload = loads(message.data, self.policy)
                await asyncio.to_thread(handle_compile_result, self.db, self.policy, payload)
                await message.ack()
            except Exception as error:  # keep the stream moving, surface the error
                print(f"[control] compile result error: {error}", flush=True)
                await message.nak()

    async def _consume_eval(self, broker: Broker) -> None:
        subject = self.policy["transport"]["eval_result_subject"]
        subscription = await broker.pull(subject, f"control-eval-{self.settings.control_node_id}")
        while not self.stop.is_set():
            message = await wait_for_message(subscription, 1.0)
            if message is None:
                continue
            try:
                payload = loads(message.data, self.policy)
                await asyncio.to_thread(handle_eval_result, self.db, self.policy, payload)
                await message.ack()
            except Exception as error:
                print(f"[control] eval result error: {error}", flush=True)
                await message.nak()

    async def _consume_fallback(self, broker: Broker) -> None:
        subject = self.policy["transport"]["fallback_subject"]
        subscription = await broker.pull(subject, f"control-fallback-{self.settings.control_node_id}")
        while not self.stop.is_set():
            message = await wait_for_message(subscription, 1.0)
            if message is None:
                continue
            try:
                fallback = loads(message.data, self.policy)
                result = fallback_to_eval_result(self.policy, fallback)
                await asyncio.to_thread(handle_eval_result, self.db, self.policy, result)
                await message.ack()
            except Exception as error:
                print(f"[control] fallback error: {error}", flush=True)
                await message.nak()

    async def _consume_heartbeat(self, broker: Broker) -> None:
        subject = self.policy["transport"]["heartbeat_subject"]
        subscription = await broker.pull(subject, f"control-heartbeat-{self.settings.control_node_id}")
        while not self.stop.is_set():
            message = await wait_for_message(subscription, 1.0)
            if message is None:
                continue
            try:
                payload = json.loads(message.data)
                await asyncio.to_thread(record_heartbeat, self.db, payload)
                await message.ack()
            except Exception as error:
                print(f"[control] heartbeat error: {error}", flush=True)
                await message.ack()

    async def _publish_loop(self, broker: Broker) -> None:
        while not self.stop.is_set():
            try:
                published = await publish_pending(self.db, broker, self.policy)
            except Exception as error:
                print(f"[control] outbox error: {error}", flush=True)
                published = 0
            await asyncio.sleep(0.1 if published else 0.3)

    async def _dispatch_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await asyncio.to_thread(dispatch_once, self.db, self.policy,
                                        alive_seconds=self.settings.stale_seconds)
            except Exception as error:
                print(f"[control] dispatch error: {error}", flush=True)
            await asyncio.sleep(0.3)

    async def _stale_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await asyncio.to_thread(mark_stale, self.db, self.settings.stale_seconds)
                reaped = await asyncio.to_thread(
                    reap_once, self.db, stale_seconds=self.settings.stale_seconds)
                if reaped:
                    print(f"[control] re-queued {reaped} stranded case(s)", flush=True)
            except Exception as error:
                print(f"[control] stale/reaper error: {error}", flush=True)
            await asyncio.sleep(self.settings.reaper_interval)

    async def run(self) -> None:
        self.db.wait_ready()
        print(f"[control] migrations: {self.db.migrate()}", flush=True)
        self.store.ensure_bucket()
        imported = import_all_problems(self.db, self.store, self.settings)
        print(f"[control] problems ready: {', '.join(imported) or '(none)'}", flush=True)
        broker = Broker(self.settings, self.policy)
        await broker.connect()
        print(f"[control] connected to {self.settings.nats_url}", flush=True)
        tasks = [
            asyncio.create_task(self._publish_loop(broker), name="outbox"),
            asyncio.create_task(self._dispatch_loop(), name="dispatch"),
            asyncio.create_task(self._consume_compile(broker), name="compile"),
            asyncio.create_task(self._consume_eval(broker), name="eval"),
            asyncio.create_task(self._consume_fallback(broker), name="fallback"),
            asyncio.create_task(self._consume_heartbeat(broker), name="heartbeat"),
            asyncio.create_task(self._stale_loop(), name="stale"),
        ]
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):  # pragma: no cover - non-main thread
                loop.add_signal_handler(signum, self.stop.set)
        await self.stop.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await broker.close()
        print("[control] stopped", flush=True)

    def request_stop(self) -> None:
        self.stop.set()
