"""Fresh loopback cluster for the refactor's stages 1–3; trusted fixtures only."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

import httpx
import psycopg
from psycopg.conninfo import make_conninfo
import redis

from scripts.n32_local_services import await_ready, export_logs, free_ports, local_services, read_config, service

ROOT = Path(__file__).resolve().parents[1]


def require(ok, detail):
    if not ok:
        raise RuntimeError(detail)


def run_batch(args, evidence, manifest, directory, pg, s3, nats_url, nats_token, credentials):
    redis_port, api_port = free_ports(2)
    token = secrets.token_hex(24)
    secrets_to_remove = [pg["password"], nats_token, *credentials.values(), token]
    env = dict(PATH=f"{Path(sys.executable).parent}{os.pathsep}{os.defpath}",
               LANG="C.UTF-8", PYTHONPATH=str(ROOT / "python"),
               ALGOOS_DB_DSN=make_conninfo(**pg),
               ALGOOS_S3_ENDPOINT=s3.meta.endpoint_url, ALGOOS_S3_ACCESS_KEY=credentials["access_key"],
               ALGOOS_S3_SECRET_KEY=credentials["secret_key"], ALGOOS_S3_BUCKET="algoos-refactor-smoke",
               ALGOOS_NATS_URL=nats_url, ALGOOS_NATS_TOKEN=nats_token,
               ALGOOS_REDIS_URL=f"redis://127.0.0.1:{redis_port}/0", ALGOOS_API_TOKEN=token,
               ALGOOS_SCHEDULING_STRATEGY="fcfs")
    # Only import A+B in the smoke stack (also on API startup).
    problems = directory / "problems"
    problems.mkdir()
    (problems / "a_plus_b").symlink_to(ROOT / "problems/a_plus_b", target_is_directory=True)
    env["ALGOOS_PROBLEMS_DIR"] = str(problems)
    children = []
    try:
        with ExitStack() as stack:
            def start(name, command, child_env=env):
                child = stack.enter_context(service(command, child_env, ROOT, directory / f"{name}.log"))
                children.append(child)
                return child

            def cli(*argv):
                with (directory / f"{argv[0]}.log").open("ab") as log:
                    subprocess.run([sys.executable, "-m", "algoos_cli.main", *argv], cwd=ROOT,
                                   env=env, stdout=log, stderr=subprocess.STDOUT, timeout=40, check=True)

            redis_env = dict(PATH=env["PATH"])
            if args.redis_lib:
                redis_env["LD_LIBRARY_PATH"] = str(args.redis_lib.resolve())
            redis_process = start("redis", [args.redis_bin, "--bind", "127.0.0.1", "--port", str(redis_port),
                                            "--save", "", "--appendonly", "no", "--dir", directory], redis_env)
            redis_client = redis.Redis.from_url(env["ALGOOS_REDIS_URL"], socket_timeout=1)
            stack.callback(redis_client.close)
            await_ready("Redis", redis_process, redis_client.ping, 10)
            cli("doctor")
            print("[通过] 四项本机基础服务与 algoos doctor", flush=True)
            cli("import-problems", "--problem", str(ROOT / "problems/a_plus_b/problem.json"))
            cli("import-problems", "--problem", str(ROOT / "problems/a_plus_b/problem.json"))
            with psycopg.connect(**pg) as db:
                versions = db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
                require(versions == [(path.name,) for path in sorted((ROOT / "migrations").glob("*.sql"))],
                        "migrations were not recorded exactly once")
                rows = db.execute("SELECT config FROM problems WHERE problem_id='a_plus_b'").fetchall()
                require(len(rows) == 1 and set(rows[0][0]["case_hashes"]) == {"01", "02"},
                        "imported JSONB problem plan differs")
            print("[通过] 真实 PostgreSQL 迁移与 A+B 重复导入", flush=True)
            manifest.update(doctor=True, migrations_and_reimport=True)

            for role, node in (("compile", "compile-01"), ("eval", "eval-01"), ("eval", "eval-02")):
                path = directory / node
                path.mkdir()
                sock = path / "worker.sock"
                cmd = [args.bin_dir / f"algoos-{role}", "--socket", sock, "--work-root", path / "native"]
                if role == "eval":
                    cmd += ["--node-id", node, "--no-cgroup", "--no-namespaces", "--no-seccomp", "--writable-root"]
                native = start(node + "-native", cmd, dict(PATH=env["PATH"]))

                def socket_ready():
                    with socket.socket(socket.AF_UNIX) as conn:
                        conn.settimeout(1)
                        conn.connect(str(sock))

                await_ready(node, native, socket_ready, 10)
                config = path / "agent.json"
                config.write_text(json.dumps(dict(node_id=node, role=role, native_socket=str(sock),
                                                  work_root=str(path / "work"), cache_root=str(path / "cache"),
                                                  max_parallel=1)))
                start(node + "-agent", [sys.executable, "-m", "algoos_cli.main", f"{role}-agent",
                                       "--config", config])
            start("control", [sys.executable, "-m", "algoos_cli.main", "control"])
            api = start("api", [sys.executable, "-m", "algoos_cli.main", "api", "--port", str(api_port)])
            client = stack.enter_context(httpx.Client(base_url=f"http://127.0.0.1:{api_port}",
                                                      headers={"Authorization": f"Bearer {token}"},
                                                      timeout=3, trust_env=False))
            await_ready("API", api, lambda: client.get("/healthz").raise_for_status(), 15)
            require(client.get("/api/v1/submissions", headers={"Authorization": ""}).status_code == 401,
                    "API did not enforce the temporary bearer token")
            require(client.get("/openapi.json").raise_for_status().json()["paths"].get("/api/v1/submissions"),
                    "OpenAPI submission route missing")
            for path in ("/", "/static/app.js", "/static/style.css"):
                client.get(path).raise_for_status()
            simulation = client.post("/api/v1/experiments/simulation",
                                     json=dict(count=10, strategies=["fcfs"]))
            require(simulation.status_code == 201, f"simulation API failed: {simulation.text}")
            recorded = simulation.json()
            item = client.get(f"/api/v1/experiments/{recorded['experiment_id']}").raise_for_status().json()
            items = client.get("/api/v1/experiments").raise_for_status().json()["items"]
            require(item["kind"] == "simulation" and item["summary"] == recorded["summary"]
                    and bool(item["metrics"]) and items[0]["summary"] == recorded["summary"],
                    "simulation JSONB round trip mismatch")
            manifest["http_checks"] = ["healthz", "authentication", "OpenAPI", "static_assets", "simulation_jsonb"]

            for name, expected in (("a_plus_b", "AC"), ("compile_error", "CE"), ("wrong_answer", "WA"),
                                   ("runtime_error", "RE"), ("time_limit", "TLE"), ("bad_source_hash", "SE")):
                source = (ROOT / "examples" / f"{'a_plus_b' if expected == 'SE' else name}.cpp").read_bytes()
                if expected == "SE":
                    source += b"\n// Separate smoke-test submission.\n"
                    s3.put_object(Bucket=env["ALGOOS_S3_BUCKET"],
                                  Key=f"source/{hashlib.sha256(source).hexdigest()}.cpp",
                                  Body=b"intentional hash mismatch isolated to this test")
                response = client.post("/api/v1/submissions", json=dict(
                    problem_id="a_plus_b", source=source.decode(), evaluation_mode="all_cases", strategy="fcfs"))
                require(response.status_code == 202, f"submit {name}: {response.text}")
                file_id = response.json()["file_id"]
                deadline = time.monotonic() + 25
                while True:
                    view = client.get(f"/api/v1/submissions/{file_id}").raise_for_status().json()
                    if view.get("verdict"):
                        break
                    require(time.monotonic() < deadline, f"{name} did not finish: {view}")
                    require(all(child.poll() is None for child in children), "cluster process exited; see logs")
                    time.sleep(0.2)
                manifest["cases"].append(dict(fixture=name, expected=expected, view=view))
                require(view["verdict"] == expected, f"{name}: expected {expected}, got {view['verdict']}")
                if expected in {"CE", "SE"}:
                    require(not view["per_test"] and view["compile_log"], "compile failure must not dispatch")
                else:
                    require([point["verdict"] for point in view["per_test"]] == [expected, expected],
                            f"{name}: per-test verdicts differ")
                    if expected == "AC":
                        require({point["node_id"] for point in view["per_test"]} == {"eval-01", "eval-02"},
                                "expected A+B cases on both local eval nodes")
                print(f"[通过] 新版 API 提交与查询：{name} → {expected}", flush=True)
    finally:
        export_logs(directory, evidence, secrets_to_remove)
        manifest["children_stopped"] = bool(children) and all(child.poll() is not None for child in children)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    bundled_redis = ROOT / "work/refactor-redis/usr"
    local_redis = bundled_redis / "bin/redis-server"
    parser.add_argument("--redis-bin", type=Path,
                        default=local_redis if local_redis.is_file() else shutil.which("redis-server"))
    parser.add_argument("--redis-lib", type=Path)
    parser.add_argument("--bin-dir", type=Path, default=ROOT / "work/refactor-native")
    args = parser.parse_args()
    evidence = ROOT / "artifacts/system" / ("refactor_smoke_" + uuid4().hex[:12])
    evidence.mkdir(parents=True)
    manifest = dict(executed_by=args.actor, cases=[], scope="local trusted fixtures; no production sandbox validation")
    credentials = {}
    try:
        require(os.getuid() != 0, "run the local fixture smoke as a normal user")
        require(args.redis_bin and args.redis_bin.is_file(), "provide executable Redis via --redis-bin")
        args.redis_bin = args.redis_bin.absolute()
        if args.redis_lib is None and args.redis_bin == local_redis:
            args.redis_lib = bundled_redis / "lib/x86_64-linux-gnu"
        args.bin_dir = args.bin_dir.resolve()
        for name in ("algoos-compile", "algoos-eval"):
            require((args.bin_dir / name).is_file(), f"missing {name}; build C++ first")
        config = read_config(ROOT, ROOT / "deploy/n32/local.json")
        with TemporaryDirectory(prefix="algoos_refactor_", dir="/tmp") as temp:
            with local_services(config, evidence, manifest, s3_credentials=credentials,
                                jetstream_file_bytes=8 << 30) as (pg, s3, url, token):
                run_batch(args, evidence, manifest, Path(temp), pg, s3, url, token, credentials)
        require(manifest["children_stopped"], "failed local service cleanup")
        manifest.update(status="PASSED", exit_code=0)
    except (Exception, KeyboardInterrupt) as error:
        manifest.update(status="FAILED", exit_code=1, error=str(error))
        print(f"新版联调未通过：{manifest['error']}", flush=True)
    paths = [path for directory in ("cpp", "python", "migrations") for path in (ROOT / directory).rglob("*")
             if path.is_file() and path.suffix in {".cpp", ".hpp", ".py", ".sql", ".txt"}]
    paths += [Path(__file__), ROOT / "scripts/n32_local_services.py", ROOT / "deploy/n32/local.json",
              ROOT / "deploy/scripts/local_cluster.sh", ROOT / "protocol/policy_v1.json", ROOT / "uv.lock"]
    paths += list((ROOT / "problems/a_plus_b").rglob("*.in")) + list((ROOT / "problems/a_plus_b").rglob("*.out"))
    paths += [ROOT / "problems/a_plus_b/problem.json", *(ROOT / f"examples/{name}.cpp" for name in
              ("a_plus_b", "compile_error", "wrong_answer", "runtime_error", "time_limit"))]
    manifest["source_sha256"] = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in sorted(paths)}
    (evidence / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print("运行证据：", evidence.relative_to(ROOT), flush=True)
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
