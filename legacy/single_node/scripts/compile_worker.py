"""Local one-task queue adapter around the independently built C++ compiler."""
import asyncio
from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import signal
import subprocess
from tempfile import TemporaryDirectory

from protocol.codec_v1 import dumps, loads
from scripts.compile_flow import utc_now
from scripts.submissions import MAX_SOURCE_BYTES

MAX_BINARY_BYTES = 16 << 20


def compile_task(task, storage, bucket, compiler, work):
    if task["type"] != "compile_task":
        raise ValueError("expected compile_task")
    result = dict(
        ver=1, type="eval_result", stage="compile", source="compiler",
        msg_id=task["msg_id"] + ":result", emitted_at=utc_now(), node_id="compile-01",
        test_id=None, sequence=None, judge_id=None, exit_code=None, signal=None,
        cpu_ms=None, wall_ms=None, mem_bytes=None, memory_source=None, out_bytes=None,
        wall_timeout=False, short_circuited=False, stderr_tail="", detail="",
        status="FINAL", verdict="SE", reason="SYSTEM_ERROR",
    )
    for key in ("file_id", "problem_id", "correlation_id", "attempt",
                "source_hash", "test_bundle_hash", "compiler_profile", "enqueue_seq"):
        result[key] = task[key]
    expected = task["source_hash"]
    # Storage transport errors propagate; the broker message stays unacknowledged.
    obj = storage.get_object(Bucket=bucket, Key=f"source/{expected.removeprefix('sha256:')}.cpp")
    with closing(obj["Body"]) as body:
        source = body.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES or "sha256:" + sha256(source).hexdigest() != expected:
        result["detail"] = "source SHA-256 or size mismatch"
        return result
    if task["compiler_profile"] != "cpp_baseline_v1":
        raise ValueError("unsupported local compiler profile")

    with TemporaryDirectory(prefix="compile_", dir=work) as directory:
        directory = Path(directory)
        path = directory / "source.cpp"
        path.write_bytes(source)
        output = directory / "out"
        with (directory / "diagnostics").open("w+b") as diagnostics:
            try:
                with subprocess.Popen([compiler, path, output], cwd=directory, start_new_session=True,
                                      stdout=subprocess.PIPE, stderr=diagnostics) as child:
                    try:
                        stdout, _ = child.communicate(timeout=task["limits"]["wall_ms"] / 1000)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        child.communicate()
                        result.update(verdict="CE", reason="COMPILE_TIMEOUT", detail="compiler wall deadline reached")
                        return result
            except OSError as error:
                result["detail"] = str(error)
                return result
            diagnostics.seek(max(0, diagnostics.tell() - 4096))
            result["stderr_tail"] = diagnostics.read(4096).decode("utf-8", errors="replace")
        if (child.returncode, stdout) == (2, b"CE\n"):
            result.update(verdict="CE", reason="COMPILE_ERROR")
            return result
        binary = output / "program"
        if (child.returncode, stdout) != (0, b"COMPILED\n") or not binary.is_file():
            result["detail"] = "compile-local failed"
            return result
        with binary.open("rb") as stream:
            data = stream.read(MAX_BINARY_BYTES + 1)
        if not 0 < len(data) <= MAX_BINARY_BYTES:
            result["detail"] = "binary exceeds local upload limit or is empty"
            return result
        bin_hash = sha256(data).hexdigest()
        storage.put_object(Bucket=bucket, Key=f"bin/{bin_hash}", Body=data)
        result.update(status="COMPILED", verdict=None, reason="NONE", bin_hash="sha256:" + bin_hash)
        return result


async def handle_compile_message(message, js, policy, storage, bucket, compiler, work):
    task = loads(message.data, policy)
    if message.subject != policy["transport"]["compile_task_subject"]:
        raise ValueError("unexpected compile subject")
    result = await asyncio.to_thread(compile_task, task, storage, bucket, compiler, work)
    await js.publish(policy["transport"]["compile_result_subject"], dumps(result, policy).encode(),
                     headers={"Nats-Msg-Id": result["msg_id"]}, timeout=3)
    await message.ack_sync(timeout=3)
