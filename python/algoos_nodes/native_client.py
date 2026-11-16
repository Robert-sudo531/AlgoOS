"""Length-prefixed JSON client for the native worker sockets."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any


class NativeError(RuntimeError):
    pass


class NativeClient:
    def __init__(self, socket_path: str, timeout: float = 30.0):
        self.socket_path = socket_path
        self.timeout = timeout

    def call(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(self.timeout)
            client.connect(self.socket_path)
            client.sendall(struct.pack(">I", len(data)) + data)
            header = self._read_exact(client, 4)
            length = struct.unpack(">I", header)[0]
            if length > 8 << 20:
                raise NativeError("native response exceeds the 8 MiB cap")
            body = self._read_exact(client, length)
        try:
            response = json.loads(body)
        except ValueError as error:
            raise NativeError(f"native response is not JSON: {error}") from error
        if not isinstance(response, dict):
            raise NativeError("native response must be a JSON object")
        return response

    @staticmethod
    def _read_exact(client: socket.socket, length: int) -> bytes:
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = client.recv(remaining)
            if not chunk:
                raise NativeError("native worker closed the connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def ping(self) -> dict[str, Any]:
        return self.call({"cmd": "ping"})


class NodeHealth:
    """Cached slot/load accounting for heartbeat messages."""

    def __init__(self, max_parallel: int):
        self.max_parallel = max_parallel
        self.running = 0
        self.load_ms = 0.0

    def start(self, estimate_ms: float) -> None:
        self.running += 1
        self.load_ms += estimate_ms

    def finish(self, estimate_ms: float) -> None:
        self.running = max(0, self.running - 1)
        self.load_ms = max(0.0, self.load_ms - estimate_ms)
