"""One local batch for source storage and durable submission lookup."""
import argparse
from pathlib import Path
from uuid import uuid4

import psycopg

from scripts.n32_local_services import local_services, read_config
from scripts.submissions import submit, lookup
from tests.n32_services import digest, fetch_verified, key_for, require, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = uuid4().hex[:12]
    evidence = root / "artifacts/system" / ("n33_submissions_" + run)
    evidence.mkdir(parents=True)
    manifest = {"executed_by": args.actor, "submissions": []}
    files = ["scripts/submissions.py", "tests/n33_submissions.py", "deploy/n32/submissions.sql",
             "deploy/n32/local.json", "scripts/n32_local_services.py", "tests/n32_services.py",
             "examples/a_plus_b.cpp", "examples/compile_error.cpp"]
    manifest["source_sha256"] = {name: digest((root / name).read_bytes()) for name in files}
    try:
        config = read_config(root, root / "deploy/n32/local.json")
        with local_services(config, evidence, manifest) as (pg, storage, _, _):
            bucket = "algoos-" + run
            storage.create_bucket(Bucket=bucket)
            with psycopg.connect(**pg) as db:
                db.execute((root / "deploy/n32/submissions.sql").read_text())
            for name in ("a_plus_b.cpp", "compile_error.cpp"):
                source = (root / "examples" / name).read_bytes()
                file_id = submit(pg, storage, bucket, "a_plus_b", source)
                row = lookup(pg, file_id)
                expected = {"file_id": file_id, "problem_id": "a_plus_b",
                            "source_hash": digest(source), "status": "RECEIVED"}
                require(row == expected, "提交记录读回不一致")
                key = key_for("source", row["source_hash"])
                require(fetch_verified(storage, bucket, key, row["source_hash"]) == source, "源码不一致")
                manifest["submissions"].append(dict(row, file_id=str(file_id), fixture=name))
                print(f"[通过] {name}：提交编号可查询，源码哈希一致")
            require(lookup(pg, uuid4()) is None, "不存在的编号不应返回记录")
            for problem_id, source in (("unknown", b"test"), ("a_plus_b", b"")):
                try:
                    submit(pg, storage, bucket, problem_id, source)
                except ValueError:
                    continue
                raise AssertionError("无效提交未被拒绝")
            with psycopg.connect(**pg) as db:
                require(db.execute("SELECT count(*) FROM algoos_submissions").fetchone()[0] == 2,
                        "无效提交不应写入数据库")
        manifest.update(status="PASSED", exit_code=0)
        print("N3.3 提交与查询验证通过")
    except Exception as error:
        manifest.update(status="FAILED", exit_code=1, error=str(error))
        print(f"验证失败：{error}")
    write_json(evidence / "manifest.json", manifest)
    print("运行证据：", evidence.relative_to(root))
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
