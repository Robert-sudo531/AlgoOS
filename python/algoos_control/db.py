"""PostgreSQL access and migration runner."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self, *, row_factory: Any = None) -> psycopg.Connection:
        return psycopg.connect(self.settings.db_dsn, row_factory=row_factory,
                               connect_timeout=5)

    @contextmanager
    def transaction(self, *, row_factory: Any = None) -> Iterator[psycopg.Connection]:
        connection = self.connect(row_factory=row_factory)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self, directory: Path | None = None) -> list[str]:
        directory = directory or self.settings.migrations_dir
        applied: list[str] = []
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
            done = {row[0] for row in connection.execute(
                "SELECT version FROM schema_migrations").fetchall()}
            for path in sorted(directory.glob("*.sql")):
                if path.name in done:
                    continue
                connection.execute(path.read_text(encoding="utf-8"))
                connection.execute("INSERT INTO schema_migrations(version) VALUES (%s)",
                                   (path.name,))
                applied.append(path.name)
        return applied

    def wait_ready(self, attempts: int = 60) -> None:
        import time

        last: Exception | None = None
        for _ in range(attempts):
            try:
                with self.transaction() as connection:
                    connection.execute("SELECT 1")
                return
            except psycopg.Error as error:  # pragma: no cover - startup wait
                last = error
                time.sleep(0.5)
        raise RuntimeError(f"database is not ready: {last}")


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value)
