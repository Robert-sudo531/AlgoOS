"""N3.2 临时服务：私有数据目录、回环地址、随机凭据、退出时回收。"""

import asyncio
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
from tempfile import TemporaryDirectory
import time

import boto3
from botocore.config import Config
import nats
import psycopg


HOST = "127.0.0.1"


def read_config(root, path):
    data = json.loads(path.read_text(encoding="utf-8"))
    required = ("postgres_bin", "postgres_share", "postgres_lib", "nats_server", "minio", "startup_timeout_s")
    if set(data) != set(required):
        raise ValueError("N3.2 配置字段不完整或含未知项")
    config = {key: (root / data[key]).resolve() for key in required[:-1]}
    seconds = data["startup_timeout_s"]
    if type(seconds) is not int or not 1 <= seconds <= 120:
        raise ValueError("startup_timeout_s 必须为 1 到 120 的整数")
    config["startup_timeout_s"] = seconds
    for path in (config["postgres_bin"] / "initdb", config["postgres_bin"] / "postgres",
                 config["nats_server"], config["minio"]):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise FileNotFoundError(f"缺少可执行文件：{path}")
    if not (config["postgres_share"] / "postgres.bki").is_file():
        raise FileNotFoundError("缺少 PostgreSQL 初始化模板 postgres.bki")
    if not config["postgres_lib"].is_dir():
        raise FileNotFoundError("缺少 PostgreSQL 运行库目录")
    return config


def free_ports(count):
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for sock in sockets:
            sock.bind((HOST, 0))
        return [sock.getsockname()[1] for sock in sockets]


@contextmanager
def service(command, env, directory, log, stop_signal=signal.SIGTERM):
    with log.open("wb") as output:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=subprocess.STDOUT, env=env, cwd=directory, start_new_session=True)
        try:
            yield child
        finally:
            if child.poll() is None:
                child.send_signal(stop_signal)
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)


def await_ready(label, child, probe, timeout):
    deadline = time.monotonic() + timeout
    last = None
    while child.poll() is None and time.monotonic() < deadline:
        try:
            return probe()
        except Exception as error:
            last = error
            time.sleep(0.2)
    if child.poll() is not None:
        raise RuntimeError(f"{label} 启动进程退出：{child.returncode}")
    raise RuntimeError(f"{label} 未就绪：{last}")


def export_logs(directory, evidence, credentials):
    for path in directory.glob("*.log"):
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 64 * 1024))
            body = stream.read(64 * 1024).decode("utf-8", errors="replace")
        for value in credentials:
            body = body.replace(value, "[redacted]")
        (evidence / path.name).write_text(body, encoding="utf-8")


async def nats_version(url, token):
    client = await nats.connect(url, token=token, connect_timeout=1, allow_reconnect=False)
    try:
        return str(client.connected_server_version)
    finally:
        await client.close()


@contextmanager
def local_services(config, evidence, manifest):
    postgres_password, minio_user, minio_password, nats_token = (secrets.token_hex(24) for _ in range(4))
    pg_port, s3_port, console_port, nats_port = free_ports(4)
    pg = {"host": HOST, "port": pg_port, "dbname": "postgres", "user": "algoos_probe",
          "password": postgres_password, "connect_timeout": 2, "sslmode": "disable",
          "options": "-c statement_timeout=5000 -c lock_timeout=3000"}
    s3_url = f"http://{HOST}:{s3_port}"
    nats_url = f"nats://{HOST}:{nats_port}"
    manifest["endpoints"] = {"postgres": f"{HOST}:{pg_port}", "s3": s3_url, "nats": nats_url}
    versions = manifest["service_versions"] = {}
    with TemporaryDirectory(prefix="algoos_n32_", dir="/tmp") as temporary:
        directory = Path(temporary)
        # 隔离 HOME 和配置，避免读取宿主的云账号或已有服务配置。
        base_env = {"PATH": os.defpath, "HOME": str(directory), "LANG": "C.UTF-8"}
        pg_env = dict(base_env, LD_LIBRARY_PATH=str(config["postgres_lib"]))
        s3_env = dict(base_env, MINIO_ROOT_USER=minio_user, MINIO_ROOT_PASSWORD=minio_password,
                      MINIO_BROWSER="off", MINIO_UPDATE="off")
        pgdata = directory / "pgdata"
        pwfile = directory / "pg-password"
        pwfile.write_text(postgres_password + "\n", encoding="ascii")
        pwfile.chmod(0o600)
        nats_conf = directory / "nats.conf"
        nats_conf.write_text(json.dumps({"host": HOST, "port": nats_port, "authorization": {"token": nats_token},
                                        "jetstream": {"store_dir": str(directory / "jetstream"),
                                                      "max_mem_store": 16 << 20, "max_file_store": 64 << 20}}),
                             encoding="utf-8")
        timeout = config["startup_timeout_s"]
        try:
            with (directory / "initdb.log").open("wb") as log:
                subprocess.run([config["postgres_bin"] / "initdb", "-D", pgdata, "-L", config["postgres_share"],
                                "--no-locale", "-E", "UTF8", "--auth=scram-sha-256", "--username=algoos_probe",
                                f"--pwfile={pwfile}"], stdout=log, stderr=subprocess.STDOUT, env=pg_env,
                               check=True, timeout=timeout)
            with ExitStack() as stack:
                postgres = stack.enter_context(service(
                    [config["postgres_bin"] / "postgres", "-D", pgdata, "-h", HOST, "-p", str(pg_port),
                     "-k", directory], pg_env, directory, directory / "postgres.log", signal.SIGINT))
                storage = stack.enter_context(service(
                    [config["minio"], "server", "--address", f"{HOST}:{s3_port}", "--console-address",
                     f"{HOST}:{console_port}", directory / "objects"], s3_env, directory, directory / "minio.log"))
                broker = stack.enter_context(service([config["nats_server"], "-c", nats_conf], base_env,
                                                       directory, directory / "nats.log"))

                def pg_ready():
                    with psycopg.connect(**pg) as db:
                        return db.execute("SHOW server_version").fetchone()[0]

                s3 = boto3.client("s3", endpoint_url=s3_url, aws_access_key_id=minio_user,
                                  aws_secret_access_key=minio_password, region_name="us-east-1",
                                  config=Config(connect_timeout=1, read_timeout=3, proxies={},
                                                retries={"total_max_attempts": 1},
                                                s3={"addressing_style": "path"},
                                                request_checksum_calculation="when_required",
                                                response_checksum_validation="when_required"))
                stack.callback(s3.close)
                versions["postgres"] = await_ready("PostgreSQL", postgres, pg_ready, timeout)
                print(f"[通过] PostgreSQL 连接：{versions['postgres']}", flush=True)
                info = await_ready("MinIO", storage, s3.list_buckets, timeout)
                versions["s3_server"] = info["ResponseMetadata"]["HTTPHeaders"].get("server", "unknown")
                versions["minio"] = subprocess.check_output([config["minio"], "--version"], env=s3_env,
                                                             text=True, timeout=5).splitlines()[0]
                print("[通过] MinIO 连接与凭据", flush=True)
                versions["nats"] = await_ready("NATS", broker, lambda: asyncio.run(nats_version(nats_url, nats_token)), timeout)
                print(f"[通过] NATS 连接：{versions['nats']}", flush=True)
                yield pg, s3, nats_url, nats_token
        except Exception as error:
            detail = str(error)
            for value in (postgres_password, minio_user, minio_password, nats_token):
                detail = detail.replace(value, "[redacted]")
            raise RuntimeError(detail) from None
        finally:
            export_logs(directory, evidence, (postgres_password, minio_user, minio_password, nats_token))
