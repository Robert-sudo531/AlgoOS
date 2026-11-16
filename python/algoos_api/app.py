"""FastAPI gateway: submissions, lookups, nodes and scheduling experiments."""

from __future__ import annotations

import base64
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from algoos_algorithms import simulate
from algoos_algorithms.simulator import NodeSpec
from algoos_algorithms.workload import generate
from algoos_control.config import Settings, load_policy
from algoos_control.db import Database
from algoos_control.problem import import_problem, load_problem
from algoos_control.storage import ObjectStore
from algoos_control.submissions import (
    cancel_user,
    lookup,
    submit,
    upsert_problem,
)
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "python" / "algoos_web" / "static"


class SubmissionRequest(BaseModel):
    problem_id: str = Field(min_length=1, max_length=64)
    source: str | None = Field(default=None, max_length=300_000)
    source_base64: str | None = Field(default=None, max_length=400_000)
    language: str = "cpp17"
    evaluation_mode: str | None = None
    strategy: str | None = None


class SimulationRequest(BaseModel):
    profile: str = "mixed"
    count: int = Field(default=200, ge=10, le=5000)
    problems: int = Field(default=6, ge=1, le=64)
    cases_per_problem: int = Field(default=10, ge=1, le=100)
    seed: int = 42
    nodes: int = Field(default=2, ge=1, le=16)
    slots: int = Field(default=2, ge=1, le=32)
    strategies: list[str] = Field(default_factory=lambda: ["fcfs", "sjf", "aging_sjf",
                                                           "earliest_finish", "cache_aware"])


class RateLimiter:
    """Small in-process token window for the submit endpoint."""

    def __init__(self, limit: int = 30, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        window = self.hits.setdefault(key, [])
        while window and window[0] <= now - self.window:
            window.pop(0)
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    policy = load_policy(settings)
    database = Database(settings)
    store = ObjectStore(settings)
    limiter = RateLimiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.wait_ready()
        database.migrate()
        store.ensure_bucket()
        for path in sorted(Path(settings.problems_dir).glob("*/problem.json")):
            problem = load_problem(path)
            upsert_problem(database, import_problem(problem, store))
        yield

    app = FastAPI(title="AlgoOS", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.store = store
    app.state.policy = policy
    app.state.limiter = limiter

    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def authorize(authorization: str | None = Header(default=None)) -> str:
        if not settings.api_token:
            return "anonymous"
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        token = authorization.removeprefix("Bearer ")
        if token != settings.api_token:
            raise HTTPException(status_code=401, detail="invalid token")
        return "api-user"

    @app.get("/healthz")
    def healthz() -> dict:
        with database.transaction() as connection:
            connection.execute("SELECT 1")
        return {"status": "ok", "node": settings.control_node_id}

    @app.get("/")
    def index():
        if not (STATIC / "index.html").is_file():
            return {"service": "algoos", "docs": "/docs"}
        return FileResponse(STATIC / "index.html")

    @app.post("/api/v1/submissions", status_code=202)
    def create_submission(request: SubmissionRequest, http: Request,
                          user: str = Depends(authorize)) -> dict:
        if not limiter.allow(user):
            raise HTTPException(status_code=429, detail="submission rate limit reached")
        source: bytes
        if request.source_base64:
            try:
                source = base64.b64decode(request.source_base64, validate=True)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=f"invalid base64 source: {error}")
        elif request.source is not None:
            source = request.source.encode("utf-8")
        else:
            raise HTTPException(status_code=400, detail="source or source_base64 is required")
        if request.language != "cpp17":
            raise HTTPException(status_code=400, detail="only cpp17 is supported")
        try:
            file_id = submit(database, store, policy, problem_id=request.problem_id,
                             source=source, user_id=user,
                             evaluation_mode=request.evaluation_mode,
                             strategy=request.strategy)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        view = lookup(database, file_id)
        return {"ver": 1, "file_id": str(file_id), "status": view["status"],
                "submitted_at": view["submitted_at"]}

    @app.get("/api/v1/submissions")
    def list_submissions(limit: int = 50, offset: int = 0,
                         user: str = Depends(authorize)) -> dict:
        limit = min(max(limit, 1), 200)
        with database.transaction(row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT file_id, problem_id, status, verdict, reason, submitted_at, "
                "finalized_at FROM submissions ORDER BY submitted_at DESC "
                "LIMIT %s OFFSET %s", (limit, max(offset, 0))).fetchall()
        return {"ver": 1, "items": [
            {"file_id": str(row["file_id"]), "problem_id": row["problem_id"],
             "status": row["status"], "verdict": row["verdict"], "reason": row["reason"],
             "submitted_at": row["submitted_at"].isoformat() if row["submitted_at"] else None,
             "finalized_at": row["finalized_at"].isoformat() if row["finalized_at"] else None}
            for row in rows]}

    @app.get("/api/v1/submissions/{file_id}")
    def get_submission(file_id: str, user: str = Depends(authorize)) -> dict:
        try:
            identifier = UUID(file_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid file_id") from error
        view = lookup(database, identifier)
        if view is None:
            raise HTTPException(status_code=404, detail="submission not found")
        return view

    @app.post("/api/v1/submissions/{file_id}/cancel")
    def cancel_submission(file_id: str, user: str = Depends(authorize)) -> dict:
        try:
            identifier = UUID(file_id)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="invalid file_id") from error
        try:
            generation = cancel_user(database, policy, identifier, origin=f"api:{user}")
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"ver": 1, "file_id": file_id, "status": "CANCELLED",
                "cancel_generation": generation}

    @app.get("/api/v1/problems")
    def list_problems(user: str = Depends(authorize)) -> dict:
        with database.transaction(row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT problem_id,title,time_limit_ms,memory_limit_mib,judge_mode,"
                "compare_mode,bundle_hash,config FROM problems ORDER BY problem_id").fetchall()
        items = []
        for row in rows:
            config = row["config"]
            items.append({
                "problem_id": row["problem_id"], "title": row["title"],
                "time_limit_ms": row["time_limit_ms"],
                "memory_limit_mib": row["memory_limit_mib"],
                "judge_mode": row["judge_mode"], "compare_mode": row["compare_mode"],
                "tests": [{"case_id": case["case_id"],
                           "out_bytes": config.get("out_bytes", {}).get(case["case_id"])}
                          for case in config["tests"]],
                "bundle_hash": row["bundle_hash"],
            })
        return {"ver": 1, "items": items}

    @app.get("/api/v1/nodes")
    def list_nodes(user: str = Depends(authorize)) -> dict:
        with database.transaction(row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT node_id,role,version,slots_total,slots_used,load_score,status,"
                "cached_hashes,last_heartbeat FROM eval_nodes ORDER BY role,node_id").fetchall()
        return {"ver": 1, "items": [
            {"node_id": row["node_id"], "role": row["role"], "version": row["version"],
             "slots_total": row["slots_total"], "slots_used": row["slots_used"],
             "load_score": row["load_score"], "status": row["status"],
             "cached_hashes": len(row["cached_hashes"] or []),
             "last_heartbeat": row["last_heartbeat"].isoformat()}
            for row in rows]}

    @app.get("/api/v1/experiments")
    def list_experiments(user: str = Depends(authorize)) -> dict:
        with database.transaction(row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT experiment_id,name,kind,status,config,summary,created_at,finished_at "
                "FROM experiments ORDER BY experiment_id DESC LIMIT 50").fetchall()
        return {"ver": 1, "items": [
            {"experiment_id": row["experiment_id"], "name": row["name"], "kind": row["kind"],
             "status": row["status"], "config": row["config"], "summary": row["summary"],
             "created_at": row["created_at"].isoformat(),
             "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None}
            for row in rows]}

    @app.get("/api/v1/experiments/{experiment_id}")
    def get_experiment(experiment_id: int, user: str = Depends(authorize)) -> dict:
        with database.transaction(row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT experiment_id,name,kind,status,config,summary FROM experiments "
                "WHERE experiment_id=%s", (experiment_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="experiment not found")
            metrics = connection.execute(
                "SELECT strategy,metric,value FROM experiment_metrics "
                "WHERE experiment_id=%s ORDER BY strategy,metric", (experiment_id,)).fetchall()
        return {"experiment_id": row["experiment_id"], "name": row["name"],
                "kind": row["kind"], "status": row["status"], "config": row["config"],
                "summary": row["summary"],
                "metrics": {f"{item['strategy']}.{item['metric']}": item["value"]
                            for item in metrics}}

    @app.post("/api/v1/experiments/simulation", status_code=201)
    def run_simulation(request: SimulationRequest, user: str = Depends(authorize)) -> dict:
        if not request.strategies:
            raise HTTPException(status_code=400, detail="at least one strategy is required")
        unknown = [name for name in request.strategies if name not in
                   policy["scheduling"]["strategies"]]
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown strategies: {unknown}")
        workload = generate(request.profile, count=request.count, problems=request.problems,
                            cases_per_problem=request.cases_per_problem, seed=request.seed)
        node_specs = [
            NodeSpec(node_id=f"sim-{index + 1:02d}", slots=request.slots,
                     cached_bundles=tuple(f"problem-{problem}"
                                          for problem in range(request.problems)
                                          if problem % request.nodes == index))
            for index in range(request.nodes)
        ]
        results = {}
        for strategy in request.strategies:
            outcome = simulate(workload.cases, node_specs, strategy)
            results[strategy] = outcome.metrics.as_dict()
        summary = {"workload": workload.name, "cases": len(workload.cases),
                   "seed": workload.seed, "metrics": results}
        config = request.model_dump()
        config["workload_seed"] = workload.seed
        with database.transaction() as connection:
            experiment_id = connection.execute(
                "INSERT INTO experiments(name,kind,config,status,summary,finished_at) "
                "VALUES (%s,'simulation',%s,'DONE',%s,now()) RETURNING experiment_id",
                (f"simulation:{request.profile}:{request.seed}",
                 Jsonb(config), Jsonb(summary))).fetchone()[0]
            for strategy, metrics in results.items():
                for metric, value in metrics.items():
                    connection.execute(
                        "INSERT INTO experiment_metrics(experiment_id,strategy,metric,value) "
                        "VALUES (%s,%s,%s,%s)", (experiment_id, strategy, metric, float(value)))
        return {"experiment_id": experiment_id, "summary": summary}

    return app


