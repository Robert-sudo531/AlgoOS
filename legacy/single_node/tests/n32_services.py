"""用独立本机服务验收源码/题包哈希、JetStream 投递和数据库提交。"""

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
import sys
from uuid import uuid4
from zipfile import ZipFile

from protocol.codec_v1 import dumps, loads


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "deploy/n32"
MAX_OBJECT_BYTES = 1 << 20


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fixture_objects():
    source = (ROOT / "examples/a_plus_b.cpp").read_bytes()
    problem = ROOT / "problems/a_plus_b"
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name in ("problem.json", "tests/01.in", "tests/01.out", "tests/02.in", "tests/02.out"):
            archive.writestr(name, (problem / name).read_bytes())
    return source, buffer.getvalue()


def key_for(kind, content_hash):
    # 哈希格式已由 v1 契约检查；ZIP 只作为本次探针的题包格式。
    suffix = ".cpp" if kind == "source" else ".zip"
    return f"{kind}/{content_hash.removeprefix('sha256:')}{suffix}"


class ContentMismatch(ValueError):
    pass


def fetch_verified(storage, bucket, key, expected):
    response = storage.get_object(Bucket=bucket, Key=key)
    with closing(response["Body"]) as body:
        content = body.read(MAX_OBJECT_BYTES + 1)
    require(len(content) <= MAX_OBJECT_BYTES, "对象超过探针读取上限")
    if digest(content) != expected:
        raise ContentMismatch(f"对象哈希不符：{key}")
    return content


def store_and_verify(storage, bucket, objects, manifest):
    storage.create_bucket(Bucket=bucket)
    for kind, content in objects.items():
        content_hash = digest(content)
        key = key_for(kind, content_hash)
        storage.put_object(Bucket=bucket, Key=key, Body=content)
        require(fetch_verified(storage, bucket, key, content_hash) == content, "读回对象内容不一致")
        manifest["objects"][kind] = {"key": key, "sha256": content_hash, "bytes": len(content)}
    bad = b"N3.2 deliberately different probe bytes\n"
    bad_key = "checks/hash-mismatch"
    storage.put_object(Bucket=bucket, Key=bad_key, Body=bad)
    try:
        fetch_verified(storage, bucket, bad_key, digest(objects["source"]))
    except ContentMismatch:
        manifest["negative_hash_check"] = "PASSED"
    else:
        raise AssertionError("应拒绝实际内容与预期哈希不符的对象")
    print("[通过] 对象上传/下载、SHA-256 和错哈希拒绝", flush=True)


def persist_receipt(pg, task, sequence):
    import psycopg
    from psycopg.types.json import Jsonb

    expected = (task["msg_id"], task["file_id"], task["source_hash"], task["test_bundle_hash"], sequence, task)
    with psycopg.connect(**pg) as db:
        db.execute("INSERT INTO n32_probe_receipts (msg_id,file_id,source_hash,test_bundle_hash,stream_sequence,payload) "
                   "VALUES (%s,%s,%s,%s,%s,%s)", (*expected[:-1], Jsonb(task)))
    # 用新连接读回，确认事务已经提交。
    with psycopg.connect(**pg) as reader:
        row = reader.execute("SELECT msg_id,file_id,source_hash,test_bundle_hash,stream_sequence,payload "
                             "FROM n32_probe_receipts WHERE msg_id=%s", (task["msg_id"],)).fetchone()
    require(row == expected, "数据库读回结果与任务不一致")


async def verify_message(pg, storage, nats_url, token, bucket, task, policy, manifest, evidence):
    import nats
    from nats.js.api import AckPolicy, ConsumerConfig, StorageType, StreamConfig

    subject = policy["transport"]["compile_task_subject"]
    stream, durable = "N32_PROBE", "n32_probe_reader"
    client = await nats.connect(nats_url, token=token, allow_reconnect=False, connect_timeout=2)
    try:
        js = client.jetstream(timeout=3)
        await js.add_stream(config=StreamConfig(name=stream, subjects=[subject], storage=StorageType.FILE,
                                                max_msgs=10, max_bytes=1 << 20))
        consumer = await js.pull_subscribe(subject, durable=durable, stream=stream,
                                           config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, ack_wait=45))
        payload = dumps(task, policy).encode("utf-8")
        ack = await js.publish(subject, payload, headers={"Nats-Msg-Id": task["msg_id"]}, timeout=3)
        require(ack.stream == stream and ack.seq > 0 and not ack.duplicate, "发布确认不符")
        messages = await consumer.fetch(1, timeout=5)
        require(len(messages) == 1, "应拉取一条编译任务")
        message = messages[0]
        received = loads(message.data, policy)
        require(message.subject == subject and message.data == payload and received == task, "拉取的任务不一致")
        require(message.metadata.sequence.stream == ack.seq and message.metadata.num_delivered == 1, "投递序号不一致")
        pending = await js.consumer_info(stream, durable)
        require(pending.num_ack_pending == 1, "任务应处于待确认状态")
        print("[通过] JetStream 文件流发布、拉取与 v1 契约", flush=True)

        for kind, field in (("source", "source_hash"), ("tests", "test_bundle_hash")):
            expected = received[field]
            data = await asyncio.to_thread(fetch_verified, storage, bucket, key_for(kind, expected), expected)
            require(len(data) == manifest["objects"][kind]["bytes"], "任务对象长度不一致")
        await asyncio.to_thread(persist_receipt, pg, received, ack.seq)
        write_json(evidence / "receipt.json", {"stream_sequence": ack.seq, "payload": received})
        manifest["database_committed_and_read_back"] = True
        still_pending = await js.consumer_info(stream, durable)
        require(still_pending.num_ack_pending == 1, "数据库提交后、ACK 前仍应有一条待确认任务")
        await message.ack_sync(timeout=3)
        after = await js.consumer_info(stream, durable)
        require(after.num_ack_pending == 0 and after.ack_floor.stream_seq == ack.seq, "服务端 ACK 状态不一致")
        manifest["message"] = {"subject": subject, "stream": stream, "stream_sequence": ack.seq,
                               "pending_before_ack": 1, "pending_after_ack": after.num_ack_pending,
                               "ack_after_commit": True}
        print("[通过] 任务对象校验 → PostgreSQL 提交和读回 → JetStream 确认", flush=True)
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--config", type=Path, default=CONFIG_DIR / "local.json")
    args = parser.parse_args()
    run_id = "n32_services_" + uuid4().hex[:12]
    evidence = ROOT / "artifacts/system" / run_id
    evidence.mkdir(parents=True, exist_ok=False)
    paths = [Path(__file__), ROOT / "scripts/n32_local_services.py", ROOT / "scripts/setup_n32.sh",
             CONFIG_DIR / "probe.sql", CONFIG_DIR / "requirements.txt", ROOT / "protocol/codec_v1.py",
             ROOT / "protocol/policy_v1.json", ROOT / "protocol/golden/compile_task.json"]
    manifest = {"run_id": run_id, "executed_by": args.actor,
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "scope": "isolated_local_foundations_probe", "objects": {}}
    try:
        from scripts.n32_local_services import local_services, read_config
        import psycopg

        manifest["source_sha256"] = {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths}
        manifest["config_sha256"] = digest(args.config.read_bytes())
        manifest["client_versions"] = {package: version(package) for package in ("boto3", "nats-py", "psycopg")}
        config = read_config(ROOT, args.config)
        source, bundle = fixture_objects()
        manifest["fixture_hashes"] = {"source": digest(source), "tests": digest(bundle)}
        policy = json.loads((ROOT / "protocol/policy_v1.json").read_text(encoding="utf-8"))
        task = json.loads((ROOT / "protocol/golden/compile_task.json").read_text(encoding="utf-8"))
        task.update(msg_id=f"{run_id}:compile", file_id=run_id, correlation_id=run_id,
                    emitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    source_hash=digest(source), test_bundle_hash=digest(bundle))
        write_json(evidence / "task.json", task)
        bucket = "algoos-" + run_id.rsplit("_", 1)[-1]
        manifest["bucket"] = bucket
        with local_services(config, evidence, manifest) as (pg, storage, url, token):
            with psycopg.connect(**pg) as db:
                db.execute((CONFIG_DIR / "probe.sql").read_text(encoding="utf-8"))
            store_and_verify(storage, bucket, {"source": source, "tests": bundle}, manifest)
            asyncio.run(verify_message(pg, storage, url, token, bucket, task, policy, manifest, evidence))
        manifest.update(status="PASSED", exit_code=0)
        print("N3.2 基础服务联通验证通过：PostgreSQL、MinIO、NATS JetStream", flush=True)
    except (Exception, KeyboardInterrupt) as error:
        manifest.update(status="FAILED", exit_code=1, error=f"{type(error).__name__}: {error}")
        print(f"N3.2 基础服务验证未通过：{manifest['error']}", file=sys.stderr, flush=True)
    write_json(evidence / "manifest.json", manifest)
    print("运行证据：", evidence.relative_to(ROOT), flush=True)
    return manifest["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
