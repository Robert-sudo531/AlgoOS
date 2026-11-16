"""AlgoOS command line."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from algoos_control.config import Settings, load_node_config
from algoos_control.db import Database
from algoos_control.problem import import_problem, load_problem
from algoos_control.storage import ObjectStore
from algoos_control.submissions import upsert_problem

API_DEFAULT = os.environ.get("ALGOOS_API_URL", "http://127.0.0.1:8080")


def _client(api: str) -> httpx.Client:
    token = os.environ.get("ALGOOS_API_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=api, headers=headers, timeout=10.0)


def command_api(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("algoos_api.app:create_app", factory=True, host=args.host, port=args.port,
                log_level=args.log_level)
    return 0


def command_control(args: argparse.Namespace) -> int:
    from algoos_control.service import ControlService

    asyncio.run(ControlService().run())
    return 0


def _run_agent(role: str, args: argparse.Namespace) -> int:
    config = load_node_config(args.config)
    if config.role != role:
        print(f"config role is {config.role}, expected {role}", file=sys.stderr)
        return 2
    Path(config.work_root).mkdir(parents=True, exist_ok=True)
    if role == "compile":
        from algoos_nodes.compile_agent import CompileAgent

        agent = CompileAgent(config)
    elif role == "eval":
        from algoos_nodes.eval_agent import EvalAgent

        agent = EvalAgent(config)
    else:
        from algoos_nodes.special_agent import SpecialAgent

        agent = SpecialAgent(config)
    asyncio.run(agent.run())
    return 0


def command_compile_agent(args: argparse.Namespace) -> int:
    return _run_agent("compile", args)


def command_eval_agent(args: argparse.Namespace) -> int:
    return _run_agent("eval", args)


def command_special_agent(args: argparse.Namespace) -> int:
    return _run_agent("special", args)


def command_import(args: argparse.Namespace) -> int:
    settings = Settings()
    database = Database(settings)
    store = ObjectStore(settings)
    database.wait_ready()
    database.migrate()
    store.ensure_bucket()
    paths = [Path(args.problem)] if args.problem else sorted(
        Path(settings.problems_dir).glob("*/problem.json"))
    for path in paths:
        problem = load_problem(path)
        imported = import_problem(problem, store)
        upsert_problem(database, imported)
        print(f"imported {problem.problem_id}: bundle={imported.bundle_hash} "
              f"checker={imported.checker_hash or '-'}")
    return 0


def command_submit(args: argparse.Namespace) -> int:
    if args.file:
        source = Path(args.file).read_text(encoding="utf-8")
    elif args.text:
        source = args.text
    else:
        source = sys.stdin.read()
    payload = {"problem_id": args.problem, "source": source, "language": "cpp17"}
    if args.strategy:
        payload["strategy"] = args.strategy
    if args.evaluation_mode:
        payload["evaluation_mode"] = args.evaluation_mode
    with _client(args.api) as client:
        response = client.post("/api/v1/submissions", json=payload)
        if response.status_code >= 400:
            print(response.text, file=sys.stderr)
            return 1
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    with _client(args.api) as client:
        response = client.get(f"/api/v1/submissions/{args.file_id}")
        if response.status_code >= 400:
            print(response.text, file=sys.stderr)
            return 1
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def command_cancel(args: argparse.Namespace) -> int:
    with _client(args.api) as client:
        response = client.post(f"/api/v1/submissions/{args.file_id}/cancel")
        if response.status_code >= 400:
            print(response.text, file=sys.stderr)
            return 1
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def command_nodes(args: argparse.Namespace) -> int:
    with _client(args.api) as client:
        response = client.get("/api/v1/nodes")
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    return 0


def command_problems(args: argparse.Namespace) -> int:
    with _client(args.api) as client:
        print(json.dumps(client.get("/api/v1/problems").json(), ensure_ascii=False, indent=2))
    return 0


def command_experiment(args: argparse.Namespace) -> int:
    if args.kind == "simulation":
        payload = {"profile": args.profile, "count": args.count, "nodes": args.nodes,
                   "seed": args.seed, "strategies": args.strategies}
        with _client(args.api) as client:
            response = client.post("/api/v1/experiments/simulation", json=payload)
            if response.status_code >= 400:
                print(response.text, file=sys.stderr)
                return 1
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        return 0
    print("only simulation experiments are exposed by the API; cluster runs use "
          "`python -m experiments.runner`", file=sys.stderr)
    return 2


def command_doctor(args: argparse.Namespace) -> int:
    settings = Settings()
    checks: list[tuple[str, bool, str]] = []
    database = Database(settings)
    try:
        database.wait_ready(attempts=3)
        checks.append(("postgres", True, "connected"))
    except Exception as error:
        checks.append(("postgres", False, str(error)))
    store = ObjectStore(settings)
    try:
        store.ensure_bucket()
        checks.append(("object-store", True, settings.s3_endpoint))
    except Exception as error:
        checks.append(("object-store", False, str(error)))
    for label, url in (("nats", settings.nats_url), ("redis", settings.redis_url)):
        try:
            import socket
            from urllib.parse import urlsplit

            parts = urlsplit(url.replace("redis://", "tcp://").replace("nats://", "tcp://"))
            with socket.create_connection((parts.hostname or "127.0.0.1", parts.port or 0),
                                          timeout=2):
                checks.append((label, True, "tcp reachable"))
        except Exception as error:
            checks.append((label, False, str(error)))
    for name, ok, detail in checks:
        print(f"[{'通过' if ok else '失败'}] {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="algoos", description="AlgoOS distributed judge")
    sub = parser.add_subparsers(dest="command", required=True)

    api = sub.add_parser("api", help="serve the HTTP gateway")
    api.add_argument("--host", default=Settings().api_host)
    api.add_argument("--port", type=int, default=Settings().api_port)
    api.add_argument("--log-level", default="info")
    api.set_defaults(func=command_api)

    control = sub.add_parser("control", help="run control-plane consumers and dispatcher")
    control.set_defaults(func=command_control)

    for name, func in (("compile-agent", command_compile_agent),
                       ("eval-agent", command_eval_agent),
                       ("special-agent", command_special_agent)):
        agent = sub.add_parser(name, help=f"run a {name.split('-')[0]} node agent")
        agent.add_argument("--config", required=True)
        agent.set_defaults(func=func)

    import_cmd = sub.add_parser("import-problems", help="import problem packages")
    import_cmd.add_argument("--problem", help="single problem.json; default all")
    import_cmd.set_defaults(func=command_import)

    submit = sub.add_parser("submit", help="submit a solution")
    submit.add_argument("--problem", required=True)
    submit.add_argument("--file")
    submit.add_argument("--text")
    submit.add_argument("--strategy")
    submit.add_argument("--evaluation-mode")
    submit.add_argument("--api", default=API_DEFAULT)
    submit.set_defaults(func=command_submit)

    status = sub.add_parser("status", help="query a submission")
    status.add_argument("file_id")
    status.add_argument("--api", default=API_DEFAULT)
    status.set_defaults(func=command_status)

    cancel = sub.add_parser("cancel", help="cancel a submission")
    cancel.add_argument("file_id")
    cancel.add_argument("--api", default=API_DEFAULT)
    cancel.set_defaults(func=command_cancel)

    nodes_cmd = sub.add_parser("nodes", help="list node heartbeats")
    nodes_cmd.add_argument("--api", default=API_DEFAULT)
    nodes_cmd.set_defaults(func=command_nodes)

    problems_cmd = sub.add_parser("problems", help="list imported problems")
    problems_cmd.add_argument("--api", default=API_DEFAULT)
    problems_cmd.set_defaults(func=command_problems)

    experiment = sub.add_parser("experiment", help="scheduling experiments")
    experiment.add_argument("kind", choices=["simulation"])
    experiment.add_argument("--profile", default="mixed")
    experiment.add_argument("--count", type=int, default=200)
    experiment.add_argument("--nodes", type=int, default=2)
    experiment.add_argument("--seed", type=int, default=42)
    experiment.add_argument("--strategies", nargs="+", default=["fcfs", "sjf", "aging_sjf",
                                                                  "earliest_finish",
                                                                  "cache_aware"])
    experiment.add_argument("--api", default=API_DEFAULT)
    experiment.set_defaults(func=command_experiment)

    doctor = sub.add_parser("doctor", help="check service connectivity")
    doctor.set_defaults(func=command_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
