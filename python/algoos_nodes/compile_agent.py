"""Compile node agent: NATS -> native compiler -> object storage -> result."""

from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from algoos_control.config import NodeConfig, Settings
from algoos_control.storage import ObjectStoreError
from algoos_protocol import loads

from .agent_base import NodeAgentBase
from .messages import compile_result

MAX_BINARY_BYTES = 16 << 20


class CompileAgent(NodeAgentBase):
    role = "compile"

    def __init__(self, config: NodeConfig, settings: Settings | None = None):
        super().__init__(config, settings)

    async def _handle(self, message) -> None:
        task = loads(message.data, self.policy)
        if task["type"] != "compile_task":
            raise ValueError("compile agent received a non-compile message")
        if task["compiler_profile"] != "cpp_baseline_v1":
            raise ValueError("unsupported compiler profile")
        profile = self.policy["compiler_profiles"][task["compiler_profile"]]
        try:
            source = await asyncio.to_thread(
                self.store.fetch_verified, "source", task["source_hash"],
                suffix=".cpp", cap=256 * 1024)
        except ObjectStoreError as error:
            # A verified size/hash failure is deterministic. Transport failures
            # still propagate and leave the broker task eligible for retry.
            message_out = compile_result(task, self.node_id, status="FINAL", verdict="SE",
                                         reason="SYSTEM_ERROR", detail=str(error))
            await self.publish(self.policy["transport"]["compile_result_subject"], message_out)
            await message.ack()
            return
        with TemporaryDirectory(prefix="compile-", dir=self.config.work_root) as directory:
            source_path = Path(directory) / "source.cpp"
            source_path.write_bytes(source)
            output_dir = Path(directory) / "out"
            output_dir.mkdir()
            spec = {
                "source_hash": task["source_hash"],
                "compiler_profile": task["compiler_profile"],
                "compiler": profile["compiler"],
                "source": str(source_path),
                "output_dir": str(output_dir),
                "platform": "linux-x86_64",
                "flags": profile["flags"],
                "limits": task["limits"],
            }
            response = await asyncio.to_thread(
                self.native.call, {"cmd": "compile", "spec": spec})
            if response.get("status") == "COMPILED":
                binary_path = Path(response["binary"])
                binary = binary_path.read_bytes()
                if not 0 < len(binary) <= MAX_BINARY_BYTES:
                    raise ValueError("compiled binary exceeds the upload cap")
                digest = await asyncio.to_thread(
                    self.store.put_content, "bin", binary)
                message_out = compile_result(
                    task, self.node_id, status="COMPILED", verdict=None, reason="NONE",
                    bin_hash=digest, build_key=response["build_key"],
                    compiler_version=response["compiler_version"],
                    cpu_ms=response.get("cpu_ms"), wall_ms=response.get("wall_ms"))
            else:
                verdict = response.get("verdict") or "CE"
                reason = response.get("reason") or "COMPILE_ERROR"
                if verdict == "SE":
                    reason = "SYSTEM_ERROR"
                message_out = compile_result(
                    task, self.node_id, status="FINAL", verdict=verdict, reason=reason,
                    stderr_tail=response.get("stderr_tail", ""),
                    detail=response.get("detail", ""),
                    cpu_ms=response.get("cpu_ms"), wall_ms=response.get("wall_ms"))
        await self.publish(self.policy["transport"]["compile_result_subject"], message_out)
        await message.ack()

    async def run(self) -> None:
        await self.setup()
        self.install_signals()
        heartbeat = asyncio.create_task(self.heartbeat_loop())
        try:
            await self._consume(self.policy["transport"]["compile_task_subject"],
                                f"compile-{self.node_id}", self._handle)
        finally:
            self.stop.set()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self.teardown()
