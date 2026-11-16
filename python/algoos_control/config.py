"""Runtime configuration for the control plane and node agents."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    db_dsn: str = field(default_factory=lambda: _env(
        "ALGOOS_DB_DSN", "postgresql://algoos:algoos@127.0.0.1:5432/algoos"))
    nats_url: str = field(default_factory=lambda: _env("ALGOOS_NATS_URL", "nats://127.0.0.1:4222"))
    nats_token: str = field(default_factory=lambda: _env("ALGOOS_NATS_TOKEN", ""))
    s3_endpoint: str = field(default_factory=lambda: _env(
        "ALGOOS_S3_ENDPOINT", "http://127.0.0.1:9000"))
    s3_access_key: str = field(default_factory=lambda: _env("ALGOOS_S3_ACCESS_KEY", "algoos"))
    s3_secret_key: str = field(default_factory=lambda: _env("ALGOOS_S3_SECRET_KEY", "algoos-secret"))
    s3_bucket: str = field(default_factory=lambda: _env("ALGOOS_S3_BUCKET", "algoos"))
    s3_region: str = field(default_factory=lambda: _env("ALGOOS_S3_REGION", "us-east-1"))
    redis_url: str = field(default_factory=lambda: _env("ALGOOS_REDIS_URL", "redis://127.0.0.1:6379/0"))
    policy_path: Path = field(default_factory=lambda: Path(
        _env("ALGOOS_POLICY", str(ROOT / "protocol" / "policy_v1.json"))))
    problems_dir: Path = field(default_factory=lambda: Path(
        _env("ALGOOS_PROBLEMS_DIR", str(ROOT / "problems"))))
    migrations_dir: Path = field(default_factory=lambda: Path(
        _env("ALGOOS_MIGRATIONS_DIR", str(ROOT / "migrations"))))
    api_token: str = field(default_factory=lambda: _env("ALGOOS_API_TOKEN", ""))
    api_host: str = field(default_factory=lambda: _env("ALGOOS_API_HOST", "127.0.0.1"))
    api_port: int = field(default_factory=lambda: int(_env("ALGOOS_API_PORT", "8080")))
    default_strategy: str = field(default_factory=lambda: _env(
        "ALGOOS_SCHEDULING_STRATEGY", "cache_aware"))
    control_node_id: str = field(default_factory=lambda: _env(
        "ALGOOS_CONTROL_NODE_ID", "control-01"))
    ensure_stream: bool = field(default_factory=lambda: _env("ALGOOS_ENSURE_STREAM", "1") == "1")


@dataclass(frozen=True)
class NodeConfig:
    """Per-node agent configuration loaded from a JSON file."""

    node_id: str
    role: str  # compile | eval | special
    native_socket: str
    work_root: str
    cache_root: str
    cache_max_bytes: int = 4 << 30
    max_parallel: int = 4
    special: bool = False


def load_node_config(path: Path | str) -> NodeConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"node_id", "role", "native_socket", "work_root", "cache_root"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"node config missing fields: {sorted(missing)}")
    role = data["role"]
    if role not in {"compile", "eval", "special"}:
        raise ValueError(f"unsupported role: {role}")
    return NodeConfig(
        node_id=data["node_id"],
        role=role,
        native_socket=data["native_socket"],
        work_root=data["work_root"],
        cache_root=data["cache_root"],
        cache_max_bytes=int(data.get("cache_max_bytes", 4 << 30)),
        max_parallel=int(data.get("max_parallel", 4)),
        special=bool(data.get("special", False)),
    )


def load_policy(settings: Settings) -> dict:
    from algoos_protocol import load_policy as load

    return load(settings.policy_path)
