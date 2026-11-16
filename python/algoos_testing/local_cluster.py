"""Ephemeral local AlgoOS cluster used by integration tests and scenario scripts.

The harness starts real PostgreSQL, Redis, NATS and MinIO containers (Docker),
the native C++ workers and the Python agents, control plane and HTTP API.  It
deliberately lives outside the release packages: integration tests must
exercise the same binaries that production uses, not mocks.

MinIO is the default object store.  ``ALGOOS_TEST_S3=moto`` switches to the
moto server (installed by the dev extras) for environments where the MinIO
image cannot be pulled; evidence produced with moto must not be described as a
MinIO validation.

Prerequisites:
  * Docker daemon reachable by the current user
  * ``build/cpp/algoos-{compile,eval,special}`` built
  * Python dev extras installed (``moto[server]`` is only needed for the
    moto fallback)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import nats
import psycopg
import redis

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build" / "cpp"
DEFAULT_PROBLEMS = ("a_plus_b", "special_pair", "short_circuit_probe")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until(predicate: Callable[[], bool], timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception as error:  # readiness probes are expected to fail early
            last = error
        time.sleep(0.15)
    detail = f" ({last})" if last else ""
    raise TimeoutError(f"{label} did not become ready within {timeout:.0f}s{detail}")


MINIO_IMAGE = ("cgr.dev/chainguard/minio@sha256:"
               "9158c67aa441627c366442e84656f689e16bec4809851ec64318561a365b69e4")


def _docker_run(name: str, image: str, ports: dict[int, int], args: Iterable[str] = (),
                env: dict[str, str] | None = None, user: str | None = None) -> None:
    command = ["docker", "run", "-d", "--rm", "--name", name]
    if user is not None:
        command += ["--user", user]
    for key, value in (env or {}).items():
        command += ["-e", f"{key}={value}"]
    for host_port, container_port in ports.items():
        command += ["-p", f"127.0.0.1:{host_port}:{container_port}"]
    command += [image, *args]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"docker run {name} failed: {result.stderr.strip()}")


def _docker_stop(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)


class LocalCluster:
    """A disposable, fully local cluster with two eval nodes and one special node."""

    def __init__(self, *, problems: Iterable[str] = DEFAULT_PROBLEMS,
                 strategy: str = "fcfs", log_dir: Path | str | None = None,
                 startup_timeout: float = 45.0, extra_env: dict[str, str] | None = None):
        self.extra_env = dict(extra_env or {})
        self.problem_ids = tuple(problems)
        self.strategy = strategy
        self.startup_timeout = startup_timeout
        self.temp = TemporaryDirectory(prefix="algoos-e2e-")
        self.root = Path(self.temp.name)
        self.logs = Path(log_dir) if log_dir else self.root / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.processes: dict[str, subprocess.Popen] = {}
        self.container_names: list[str] = []
        self.node_configs: dict[str, Path] = {}
        self._moto = None
        self._redis: redis.Redis | None = None
        self._client: httpx.Client | None = None
        self.pg_port = 0
        self.redis_port = 0
        self.nats_port = 0
        self.s3_port = 0
        self.s3_mode = os.environ.get("ALGOOS_TEST_S3", "minio").strip().lower()
        self.s3_image = ""
        self.s3_access_key = "test"
        self.s3_secret_key = "test"
        self.api_port = 0
        self.bucket = f"algoos-test-{uuid.uuid4().hex[:10]}"
        self.api_token = uuid.uuid4().hex
        self.env: dict[str, str] = {}

    # ------------------------------------------------------------------ setup
    def __enter__(self) -> LocalCluster:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def start(self) -> None:
        self._check_prerequisites()
        self._start_services()
        self._build_env()
        self._prepare_problems()
        self._import_problems()
        self._start_workers()
        self._start_agents()
        self._start_control_and_api()
        self._wait_for_cluster()
        self.nodes = self.list_nodes()

    def _check_prerequisites(self) -> None:
        for name in ("algoos-compile", "algoos-eval", "algoos-special"):
            if not (BUILD / name).is_file():
                raise RuntimeError(
                    f"missing {BUILD / name}; build with "
                    "`cmake -S cpp -B build/cpp -G Ninja && cmake --build build/cpp`"
                )
        if shutil.which("docker") is None:
            raise RuntimeError("docker is required for integration tests")
        if shutil.which("g++") is None:
            raise RuntimeError("g++ is required for the compile worker")
        subprocess.run(["docker", "info"], capture_output=True, check=True)

    def _start_services(self) -> None:
        self.pg_port = _free_port()
        self.redis_port = _free_port()
        self.nats_port = _free_port()
        self.s3_port = _free_port()
        self.api_port = _free_port()
        suffix = uuid.uuid4().hex[:8]
        self.pg_name = f"algoos-e2e-pg-{suffix}"
        self.redis_name = f"algoos-e2e-redis-{suffix}"
        self.nats_name = f"algoos-e2e-nats-{suffix}"
        # Local mirror images verified in the project environment.  Override
        # with ALGOOS_*_IMAGE when a different registry is reachable.
        pg_image = os.environ.get("ALGOOS_PG_IMAGE",
                                  "docker.m.daocloud.io/library/postgres:17-alpine")
        redis_image = os.environ.get("ALGOOS_REDIS_IMAGE",
                                     "docker.m.daocloud.io/library/redis:7.4-alpine")
        nats_image = os.environ.get("ALGOOS_NATS_IMAGE",
                                    "docker.m.daocloud.io/library/nats:2.10-alpine")
        _docker_run(self.pg_name, pg_image, {self.pg_port: 5432},
                    env={"POSTGRES_USER": "algoos", "POSTGRES_PASSWORD": "algoos",
                         "POSTGRES_DB": "algoos"})
        _docker_run(self.redis_name, redis_image, {self.redis_port: 6379},
                    args=["redis-server", "--save", "", "--appendonly", "no"])
        _docker_run(self.nats_name, nats_image, {self.nats_port: 4222},
                    args=["-js", "-sd", "/data"])
        self.container_names = [self.pg_name, self.redis_name, self.nats_name]

        def pg_ready() -> bool:
            with psycopg.connect(host="127.0.0.1", port=self.pg_port, user="algoos",
                                 password="algoos", dbname="algoos", connect_timeout=1):
                return True

        _wait_until(pg_ready, self.startup_timeout, "PostgreSQL")
        self._redis = redis.Redis(host="127.0.0.1", port=self.redis_port,
                                  decode_responses=True, socket_timeout=1)
        _wait_until(lambda: bool(self._redis.ping()), self.startup_timeout, "Redis")
        _wait_until(lambda: self._nats_ready(), self.startup_timeout, "NATS")

        if self.s3_mode == "minio":
            self.s3_image = os.environ.get("ALGOOS_MINIO_IMAGE", MINIO_IMAGE)
            self.s3_access_key = "algoos"
            self.s3_secret_key = "algoos-secret"
            s3_name = f"algoos-e2e-minio-{suffix}"
            _docker_run(
                s3_name, self.s3_image, {self.s3_port: 9000},
                args=["server", "/data", "--address", ":9000",
                      "--console-address", ":9001"],
                env={"MINIO_ROOT_USER": self.s3_access_key,
                     "MINIO_ROOT_PASSWORD": self.s3_secret_key,
                     "MINIO_BROWSER": "off"},
            )
            self.container_names.append(s3_name)
            _wait_until(self._s3_ready, self.startup_timeout,
                        f"S3 (MinIO {self.s3_image})")
        elif self.s3_mode == "moto":
            from moto.server import ThreadedMotoServer

            self._moto = ThreadedMotoServer(ip_address="127.0.0.1", port=self.s3_port,
                                            verbose=False)
            self._moto.start()
            time.sleep(0.3)
            _wait_until(self._s3_ready, self.startup_timeout, "S3 (moto)")
        else:
            raise RuntimeError(
                f"unknown ALGOOS_TEST_S3={self.s3_mode!r}; use 'minio' or 'moto'")

    def _nats_ready(self) -> bool:
        async def probe() -> bool:
            client = await nats.connect(f"nats://127.0.0.1:{self.nats_port}",
                                        connect_timeout=1, allow_reconnect=False)
            await client.close()
            return True

        try:
            return asyncio.run(probe())
        except Exception:
            return False

    def _s3_ready(self) -> bool:
        import boto3
        from botocore.config import Config

        client = boto3.client("s3", endpoint_url=f"http://127.0.0.1:{self.s3_port}",
                              aws_access_key_id=self.s3_access_key,
                              aws_secret_access_key=self.s3_secret_key,
                              region_name="us-east-1",
                              config=Config(connect_timeout=1, read_timeout=1, proxies={},
                                            retries={"total_max_attempts": 1},
                                            s3={"addressing_style": "path"}))
        try:
            client.list_buckets()
            with contextlib.suppress(Exception):
                client.create_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False
        finally:
            client.close()

    def _build_env(self) -> None:
        # Keep the caller's PATH so the compile worker can find the same g++.
        gxx_dir = str(Path(shutil.which("g++") or "/usr/bin").parent)
        path_parts = [str(Path(sys.executable).parent), gxx_dir, os.defpath]
        self.env = {
            "PATH": os.pathsep.join(path_parts),
            "LANG": "C.UTF-8",
            "PYTHONPATH": str(ROOT / "python"),
            "ALGOOS_DB_DSN": f"postgresql://algoos:algoos@127.0.0.1:{self.pg_port}/algoos",
            "ALGOOS_S3_ENDPOINT": f"http://127.0.0.1:{self.s3_port}",
            "ALGOOS_S3_ACCESS_KEY": self.s3_access_key,
            "ALGOOS_S3_SECRET_KEY": self.s3_secret_key,
            "ALGOOS_S3_BUCKET": self.bucket,
            "ALGOOS_NATS_URL": f"nats://127.0.0.1:{self.nats_port}",
            "ALGOOS_REDIS_URL": f"redis://127.0.0.1:{self.redis_port}/0",
            "ALGOOS_API_TOKEN": self.api_token,
            "ALGOOS_SCHEDULING_STRATEGY": self.strategy,
            "ALGOOS_PROBLEMS_DIR": str(self.root / "problems"),
        }
        self.env.update(self.extra_env)

    def _prepare_problems(self) -> None:
        directory = self.root / "problems"
        directory.mkdir()
        for problem_id in self.problem_ids:
            source = ROOT / "problems" / problem_id
            if not source.is_dir():
                raise FileNotFoundError(f"missing problem package: {source}")
            os.symlink(source, directory / problem_id, target_is_directory=True)

    def _import_problems(self) -> None:
        self.run_cli("import-problems")

    # --------------------------------------------------------------- processes
    def _spawn(self, name: str, command: list[str], *, env: dict[str, str] | None = None,
               cwd: Path | None = None) -> subprocess.Popen:
        log = (self.logs / f"{name}.log").open("ab")
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            env=dict(self.env if env is None else env), cwd=cwd or ROOT,
            start_new_session=True,
        )
        self.processes[name] = process
        return process

    def run_cli(self, *args: str, timeout: float = 60.0) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "algoos_cli.main", *args], cwd=ROOT, env=self.env,
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"algoos {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
            )

    def _wait_socket(self, path: Path, label: str) -> None:
        def probe() -> bool:
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(1)
                client.connect(str(path))
            return True

        _wait_until(probe, self.startup_timeout, label)

    def _start_workers(self) -> None:
        native = self.root / "native"
        native.mkdir()
        common = ["--workers", "2"]
        disabled = ["--no-cgroup", "--no-namespaces", "--no-seccomp", "--writable-root"]
        sock = native / "compile.sock"
        self._spawn("compile-worker", [str(BUILD / "algoos-compile"), "--socket", str(sock),
                                       "--work-root", str(native / "compile"), *common])
        self._wait_socket(sock, "compile worker")
        for node in ("eval-01", "eval-02"):
            sock = native / f"{node}.sock"
            self._spawn(f"{node}-worker", [str(BUILD / "algoos-eval"), "--socket", str(sock),
                                           "--node-id", node, "--work-root",
                                           str(native / node), *common, *disabled])
            self._wait_socket(sock, f"{node} worker")
        sock = native / "special.sock"
        self._spawn("special-worker", [str(BUILD / "algoos-special"), "--socket", str(sock),
                                       "--work-root", str(native / "special"), *common,
                                       *disabled])
        self._wait_socket(sock, "special worker")

    def _start_agents(self) -> None:
        configs = self.root / "configs"
        configs.mkdir()
        nodes = (("compile-01", "compile"), ("eval-01", "eval"), ("eval-02", "eval"),
                 ("special-01", "special"))
        for node_id, role in nodes:
            config = configs / f"{node_id}.json"
            config.write_text(json.dumps({
                "node_id": node_id, "role": role,
                "native_socket": str(self.root / "native" /
                                     ("compile.sock" if role == "compile" else
                                      f"{node_id}.sock" if role != "special" else "special.sock")),
                "work_root": str(self.root / "work" / node_id),
                "cache_root": str(self.root / "cache" / node_id),
                "cache_max_bytes": 1 << 30, "max_parallel": 2,
            }, indent=2) + "\n", encoding="utf-8")
            self.node_configs[node_id] = config
            self._spawn(f"{node_id}-agent", [sys.executable, "-m", "algoos_cli.main",
                                             f"{role}-agent", "--config", str(config)])
        _wait_until(lambda: all(process.poll() is None for process in self.processes.values()),
                    self.startup_timeout, "node agents")

    def _start_control_and_api(self) -> None:
        self._spawn("control", [sys.executable, "-m", "algoos_cli.main", "control"])
        self._spawn("api", [sys.executable, "-m", "algoos_cli.main", "api", "--host",
                            "127.0.0.1", "--port", str(self.api_port)])
        self._client = httpx.Client(base_url=f"http://127.0.0.1:{self.api_port}",
                                    headers={"Authorization": f"Bearer {self.api_token}"},
                                    timeout=10.0, trust_env=False)
        _wait_until(lambda: self._client.get("/healthz").status_code == 200,
                    self.startup_timeout, "HTTP API")

    def _wait_for_cluster(self) -> None:
        def heartbeats_ready() -> bool:
            with psycopg.connect(self.env["ALGOOS_DB_DSN"], connect_timeout=2) as connection:
                rows = connection.execute(
                    "SELECT node_id FROM eval_nodes WHERE role='eval' AND status='ALIVE'"
                ).fetchall()
            return {row[0] for row in rows} == {"eval-01", "eval-02"}

        _wait_until(heartbeats_ready, self.startup_timeout, "eval node heartbeats")
        _wait_until(lambda: all(
            self._redis.exists(f"algoos:heartbeat:{node}") for node in
            ("eval-01", "eval-02", "special-01")), self.startup_timeout,
            "Redis heartbeats")

    # ------------------------------------------------------------------ teardown
    def stop(self) -> None:
        for _name, process in list(self.processes.items()):
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        self.processes.clear()
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._moto is not None:
            with contextlib.suppress(Exception):
                self._moto.stop()
            self._moto = None
        for name in self.container_names:
            _docker_stop(name)
        self.container_names.clear()
        if self._redis is not None:
            with contextlib.suppress(Exception):
                self._redis.close()
            self._redis = None
        self.temp.cleanup()

    # -------------------------------------------------------------------- client
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            raise RuntimeError("cluster is not running")
        return self._client

    @property
    def redis_client(self) -> redis.Redis:
        if self._redis is None:
            raise RuntimeError("cluster is not running")
        return self._redis

    def submit(self, problem_id: str, source: str, *, evaluation_mode: str | None = None,
               strategy: str | None = None) -> str:
        response = self.client.post("/api/v1/submissions", json={
            "problem_id": problem_id, "source": source, "language": "cpp17",
            "evaluation_mode": evaluation_mode, "strategy": strategy,
        })
        if response.status_code != 202:
            raise RuntimeError(f"submission rejected: {response.status_code} {response.text}")
        return response.json()["file_id"]

    def view(self, file_id: str) -> dict:
        response = self.client.get(f"/api/v1/submissions/{file_id}")
        response.raise_for_status()
        return response.json()

    def wait_for(self, file_id: str, predicate: Callable[[dict], bool], *,
                 timeout: float = 60.0, label: str = "submission") -> dict:
        def poll() -> bool:
            for name, process in self.processes.items():
                if process.poll() is not None:
                    raise RuntimeError(f"{name} exited with {process.returncode}; see {self.logs}")
            return predicate(self.view(file_id))

        _wait_until(poll, timeout, label)
        return self.view(file_id)

    def wait_terminal(self, file_id: str, *, timeout: float = 60.0) -> dict:
        return self.wait_for(
            file_id,
            lambda view: view["status"] in {"FINAL", "CE", "CANCELLED"},
            timeout=timeout, label="terminal submission",
        )

    def wait_cases_terminal(self, file_id: str, *, timeout: float = 60.0) -> dict:
        """Wait for the submission and every materialised test point to converge.

        A user cancel currently finalises the submission immediately while the
        in-flight case results arrive asynchronously; scenario scripts must wait
        for both layers before asserting per-test verdicts.
        """
        return self.wait_for(
            file_id,
            lambda view: view["status"] in {"FINAL", "CE", "CANCELLED"}
            and all(point["state"] in {"DONE", "CANCELLED"}
                    for point in view["per_test"]),
            timeout=timeout, label="terminal submission and cases",
        )

    def cancel(self, file_id: str) -> dict:
        response = self.client.post(f"/api/v1/submissions/{file_id}/cancel")
        if response.status_code >= 400:
            raise RuntimeError(f"cancel failed: {response.status_code} {response.text}")
        return response.json()

    def list_nodes(self) -> list[dict]:
        response = self.client.get("/api/v1/nodes")
        response.raise_for_status()
        return response.json()["items"]

    def publish(self, subject: str, payload: dict) -> None:
        """Publish a raw JSON payload through JetStream (used for fault tests)."""
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

        async def send() -> None:
            client = await nats.connect(self.env["ALGOOS_NATS_URL"], connect_timeout=2)
            try:
                js = client.jetstream()
                await js.publish(subject, body, headers={"Nats-Msg-Id": payload["msg_id"]})
            finally:
                await client.close()

        asyncio.run(send())

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with psycopg.connect(self.env["ALGOOS_DB_DSN"], connect_timeout=2) as connection:
            return connection.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple = ()) -> None:
        with psycopg.connect(self.env["ALGOOS_DB_DSN"], connect_timeout=2) as connection:
            connection.execute(sql, params)

    def put_object(self, key: str, data: bytes) -> str:
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3", endpoint_url=self.env["ALGOOS_S3_ENDPOINT"],
            aws_access_key_id=self.env["ALGOOS_S3_ACCESS_KEY"],
            aws_secret_access_key=self.env["ALGOOS_S3_SECRET_KEY"],
            region_name="us-east-1",
            config=Config(connect_timeout=2, read_timeout=5, proxies={},
                          retries={"total_max_attempts": 1},
                          s3={"addressing_style": "path"}))
        try:
            client.put_object(Bucket=self.bucket, Key=key, Body=data)
        finally:
            client.close()
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def stop_process(self, name: str, *, force: bool = False) -> None:
        process = self.processes.pop(name)
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            try:
                process.wait(timeout=5 if not force else 2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    def start_process(self, name: str, command: list[str]) -> None:
        self._spawn(name, command)

    def restart_agent(self, node_id: str, role: str) -> None:
        if f"{node_id}-agent" in self.processes:
            self.stop_process(f"{node_id}-agent")
        config = self.node_configs[node_id]
        self._spawn(f"{node_id}-agent", [sys.executable, "-m", "algoos_cli.main",
                                         f"{role}-agent", "--config", str(config)])

    def dump_logs(self, destination: Path | str) -> None:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        for path in self.logs.glob("*.log"):
            target = destination / path.name
            if path.resolve() == target.resolve():
                continue
            shutil.copy2(path, target)
