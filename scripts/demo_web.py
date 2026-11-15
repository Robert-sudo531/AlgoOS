"""Loopback-only browser demo. Queries trusted local evidence; runs fixed fixtures only."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
from threading import Lock, Thread
from urllib.parse import urlsplit
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web"
EVIDENCE = ROOT / "artifacts/system"


def load_records(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != "PASSED":
        raise ValueError("只接受通过验收的记录")
    if data.get("replay") == "replay/manifest.json":
        return load_records(path.parent / data["replay"])
    evaluated = {item["file_id"]: item for item in data["evaluations"]}
    records = {}
    for case in data["cases"]:
        task, result = case["task"], case["result"]
        file_id = str(UUID(task["file_id"]))
        evaluation = evaluated.get(file_id)
        points = evaluation["results"] if evaluation else []
        records[file_id] = dict(
            id=file_id, sample=case["fixture"], compile_status=case["status"],
            verdict=evaluation["status"] if evaluation else case["status"],
            reason=result["reason"], diagnostic=result["stderr_tail"] or result["detail"],
            dispatched=len(case["eval_tasks"]),
            tests=[{key: item.get(key) for key in ("test_id", "node_id", "verdict", "reason", "wall_ms",
                                                   "cpu_ms", "mem_bytes", "memory_source")}
                   for item in sorted(points, key=lambda item: item["sequence"])],
        )
    if not records:
        raise ValueError("验收记录没有提交")
    return records


def latest_evidence(pattern="n4_handoff_*/manifest.json"):
    if EVIDENCE.exists():
        paths = sorted(EVIDENCE.glob(pattern),
                       key=lambda path: path.stat().st_mtime, reverse=True)
        for path in paths:
            try:
                load_records(path)
                return path
            except (ValueError, OSError, KeyError, TypeError):
                continue
    return None


class Demo:
    def __init__(self, evidence):
        self.lock = Lock()
        self.records = load_records(evidence) if evidence else {}
        self.origin = evidence.parent.name if evidence else None
        self.mode = "replay"
        self.state = "idle"
        self.detail = ""
        self.runner = None
        self.python = ROOT / "work/n32_runtime/venv/bin/python"

    def run(self):
        try:
            proc = subprocess.run([self.python, "-m", "tests.n34_eval_batch", "--two-nodes",
                                   "--actor", "web-demo"], cwd=ROOT, capture_output=True, text=True)
            line = next((line for line in reversed(proc.stdout.splitlines())
                         if line.startswith("运行证据：")), "")
            name = line.partition("：")[2].strip()
            path = (ROOT / name).resolve()
            if proc.returncode or not path.is_relative_to(EVIDENCE) or not path.name.startswith("n35_two_nodes_"):
                raise RuntimeError("内置批次未通过；请检查终端中的批次输出")
            records = load_records(path / "manifest.json")
            with self.lock:
                self.records, self.origin = records, path.name
                self.mode, self.state = "live", "passed"
        except Exception as error:
            with self.lock:
                self.state, self.detail = "failed", str(error)
        finally:
            if "proc" in locals():
                print(proc.stdout, proc.stderr, sep="", end="", flush=True)

    def start(self):
        with self.lock:
            if self.state == "running":
                return 409, {"error": "已有样例在运行"}
            if not self.python.is_file():
                return 503, {"error": "请先运行 bash scripts/setup_n32.sh 准备本机依赖"}
            self.state, self.detail = "running", ""
            self.runner = Thread(target=self.run)
            self.runner.start()
            return 202, {"status": "running"}


def handler_for(demo):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json(self, status, payload):
            self.respond(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def local_host(self):
            port = self.server.server_port
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def do_GET(self):
            if not self.local_host():
                return self.json(403, {"error": "请使用本机地址"})
            path = urlsplit(self.path).path
            assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                      "/style.css": ("style.css", "text/css")}
            if path in assets:
                name, kind = assets[path]
                return self.respond(200, (ASSETS / name).read_bytes(), kind + "; charset=utf-8")
            if path == "/api/contest":
                contest = latest_evidence("n4_lazy_gold_*/manifest.json")
                return self.json(200, dict(items=list(load_records(contest).values()) if contest else [],
                                           evidence=contest.parent.name if contest else None))
            with demo.lock:
                if path == "/api/submissions":
                    return self.json(200, dict(items=list(demo.records.values()), evidence=demo.origin,
                                               mode=demo.mode, state=demo.state, detail=demo.detail))
                if path.startswith("/api/submissions/"):
                    item = demo.records.get(path.removeprefix("/api/submissions/"))
                    if item:
                        return self.json(200, item)
            self.json(404, {"error": "未找到"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self.local_host() or (origin and origin != "http://" + self.headers["Host"]):
                return self.json(403, {"error": "仅允许本机演示请求"})
            if self.path != "/api/demo/run":
                return self.json(404, {"error": "未找到"})
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
                return self.json(400, {"error": "只运行内置样例，不接受提交内容"})
            status, body = demo.start()
            self.json(status, body)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--evidence", type=Path, help="已通过的本机验收 manifest.json")
    args = parser.parse_args()
    demo = Demo(args.evidence or latest_evidence())
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(demo)) as server:
        print(f"AlgoOS 本机演示：http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            if demo.runner and demo.runner.is_alive():
                print("等待当前内置批次退出…", flush=True)
                demo.runner.join()


if __name__ == "__main__":
    main()
