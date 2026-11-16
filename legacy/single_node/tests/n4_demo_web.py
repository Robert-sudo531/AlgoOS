"""Verify the loopback HTTP rehearsal, including an actual fixed-fixture run."""
import argparse
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4
import json

from scripts.demo_web import Demo, EVIDENCE, handler_for, latest_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    evidence = EVIDENCE / ("n4_web_" + uuid4().hex[:12])
    evidence.mkdir(parents=True)
    result = dict(executed_by=args.actor, checks=[])
    try:
        demo = Demo(latest_evidence())
        with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(demo)) as server:
            thread = Thread(target=server.serve_forever)
            thread.start()

            def request(method, path, code, **kwargs):
                conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                try:
                    conn.request(method, path, **kwargs)
                    response = conn.getresponse()
                    body = response.read()
                    assert response.status == code, (path, response.status, body)
                    return body
                finally:
                    conn.close()

            def check_records(data):
                items = data["items"]
                assert [(x["verdict"], x["dispatched"]) for x in items] == [
                    ("AC", 2), ("WA", 2), ("CE", 0), ("SE", 0)]
                for item in items:
                    detail = json.loads(request("GET", "/api/submissions/" + item["id"], 200))
                    assert detail == item
                    assert [test["node_id"] for test in item["tests"]] == (
                        ["eval-01", "eval-02"] if item["dispatched"] else [])

            try:
                assert "评测结果演示".encode() in request("GET", "/", 200)
                request("GET", "/app.js", 200)
                request("GET", "/style.css", 200)
                check_records(json.loads(request("GET", "/api/submissions", 200)))
                contest = json.loads(request("GET", "/api/contest", 200))
                if contest["items"]:
                    item, = contest["items"]
                    assert (item["verdict"], item["dispatched"]) == ("AC", 11)
                    points = item["tests"]
                    assert [p["test_id"] for p in points] == [str(i) for i in range(1, 12)]
                    assert [p["node_id"] for p in points].count("eval-01") == 6
                    assert [p["node_id"] for p in points].count("eval-02") == 5
                    assert all(p["verdict"] == "AC" and p["cpu_ms"] is not None
                               and p["wall_ms"] is not None and p["mem_bytes"] is not None
                               and p["memory_source"] == "rss_peak" for p in points)
                    result["contest_evidence"] = contest["evidence"]
                    print("[通过] 竞赛题 HTTP 结果、节点点数和资源指标", flush=True)
                request("GET", "/api/submissions/missing", 404)
                request("POST", "/api/demo/run", 403, headers={"Origin": "http://example.invalid"})
                request("POST", "/api/demo/run", 400, body=b"source not allowed")
                result["checks"].append("页面资源、回放、详情及无效请求通过")
                print("[通过] 页面资源、回放、详情及无效请求", flush=True)
                request("POST", "/api/demo/run", 202)
                request("POST", "/api/demo/run", 409)
                deadline = monotonic() + 170
                while monotonic() < deadline:
                    data = json.loads(request("GET", "/api/submissions", 200))
                    if data["state"] != "running":
                        break
                    sleep(2)
                assert data["state"] == "passed" and data["mode"] == "live", data["detail"]
                check_records(data)
                result["live_evidence"] = data["evidence"]
                result["checks"].append("真实双节点运行及结果查询通过")
            finally:
                server.shutdown()
                thread.join()
                if demo.runner:
                    demo.runner.join()
        result.update(status="PASSED", exit_code=0)
        print("N4 本机 Web/API 批次验证通过")
    except Exception as exc:
        result.update(status="FAILED", exit_code=1, error=str(exc))
        print(f"验证失败：{exc}")
    (evidence / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("运行证据：", evidence.relative_to(Path(__file__).resolve().parents[1]))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
