"""Hash-addressed local object cache with size-bounded eviction."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path

from algoos_control.storage import ObjectStore, hex_of


class CacheError(RuntimeError):
    pass


CATEGORY_SUFFIX = {"bin": "", "bundle": ".zip", "checker": ".bin", "input": ".in",
                   "answer": ".out", "output": ".out"}
CATEGORY_PREFIX = {"bin": "bin", "bundle": "tests", "checker": "checker", "input": "input",
                   "answer": "answer", "output": "outobj"}


def digest_bytes(data: bytes) -> str:
    return "sha256:" + sha256(data).hexdigest()


class LocalCache:
    def __init__(self, root: str | Path, max_bytes: int = 4 << 30):
        self.root = Path(root)
        self.max_bytes = max_bytes
        for category in CATEGORY_SUFFIX:
            (self.root / category).mkdir(parents=True, exist_ok=True)

    def path_for(self, category: str, digest: str) -> Path:
        return self.root / category / f"{hex_of(digest)}{CATEGORY_SUFFIX[category]}"

    def has(self, category: str, digest: str) -> bool:
        path = self.path_for(category, digest)
        return path.is_file()

    def fetch(self, store: ObjectStore, category: str, digest: str, cap: int) -> Path:
        path = self.path_for(category, digest)
        if path.is_file():
            data = path.read_bytes()
            if digest_bytes(data) == digest:
                if category in {"bin", "checker"} and not path.stat().st_mode & 0o100:
                    path.chmod(0o700)
                return path
            path.unlink()
        data = store.fetch_verified(CATEGORY_PREFIX[category], digest,
                                     suffix=CATEGORY_SUFFIX[category], cap=cap)
        self._write_atomic(path, data)
        self.evict()
        return path

    def put(self, category: str, digest: str, data: bytes) -> Path:
        if digest_bytes(data) != digest:
            raise CacheError("content hash mismatch before caching")
        path = self.path_for(category, digest)
        self._write_atomic(path, data)
        self.evict()
        return path

    def _write_atomic(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".part-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                if path.parent.name in {"bin", "checker"}:
                    os.fchmod(stream.fileno(), 0o700)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def evict(self) -> None:
        total = 0
        entries = []
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.startswith(".part-"):
                stat = path.stat()
                total += stat.st_size
                entries.append((stat.st_mtime, stat.st_size, path))
        if total <= self.max_bytes:
            return
        for _, size, path in sorted(entries):
            try:
                path.unlink()
                total -= size
            except FileNotFoundError:
                continue
            if total <= self.max_bytes:
                break

    def hashes(self) -> list[str]:
        result = []
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.startswith(".part-"):
                name = path.stem if path.suffix else path.name
                if len(name) == 64 and all(c in "0123456789abcdef" for c in name):
                    result.append(f"sha256:{name}")
        return sorted(result)

    def stats(self) -> dict[str, int]:
        count = 0
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                count += 1
                total += path.stat().st_size
        return {"files": count, "bytes": total}


def cached_entries(paths: Iterable[Path]) -> list[str]:  # pragma: no cover - helper
    return [digest_bytes(path.read_bytes()) for path in paths]
