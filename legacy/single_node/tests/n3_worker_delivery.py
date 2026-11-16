"""独立构建、解包到源码外、检查两份节点配置并重放真实判定。"""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory

from protocol.codec_v1 import dumps, loads
from tests.n3_local_core import sha256, write_json


def verify_worker_delivery(root, work, evidence, run_id, manifest, cases):
    originals = [item for item in cases if item.get("status") == "PASSED"]
    build_dir = work / "worker_build"
    command = ["make", "-C", str(root / "judge/native"), f"WORKER_BUILD_DIR={build_dir}", "eval-worker-package"]
    with (evidence / "build.txt").open("a", encoding="utf-8") as log:
        print(json.dumps(command, ensure_ascii=False), file=log, flush=True)
        proc = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120)
    manifest["builds"].append({"command": command, "exit_code": proc.returncode})
    if proc.returncode:
        raise RuntimeError("Worker 构建或打包失败，见 build.txt")
    archives = list((build_dir / "packages").glob("*.tar.gz"))
    if len(archives) != 1:
        raise AssertionError("应生成一份 Worker 归档")
    archive = archives[0]
    delivery = {"archive": str(archive.relative_to(root)), "sha256": sha256(archive), "node_checks": []}
    manifest["delivery"] = delivery

    with TemporaryDirectory(prefix="algoos_worker_delivery_", dir="/tmp") as directory:
        outside = Path(directory)
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(outside, filter="data")  # 本轮本机构建的可信归档。
        installed = outside / archive.name.removesuffix(".tar.gz")
        package = json.loads((installed / "manifest.json").read_text(encoding="utf-8"))
        delivery["package"] = package
        delivery["installed_outside_repo"] = not installed.is_relative_to(root)
        if (not delivery["installed_outside_repo"] or package["role"] != "eval-worker"
                or package["scope"] != "local_one_shot" or package["protocol_version"] != 1
                or not package["runtime_libraries"]):
            raise AssertionError("Worker 归档元数据不符")
        expected_files = {"bin/eval-worker", "conf/eval-worker.example.json", "share/policy_v1.json"}
        if set(package["sha256"]) != expected_files or any(
            sha256(installed / name) != digest for name, digest in package["sha256"].items()
        ):
            raise AssertionError("归档文件哈希不一致")
        binary = installed / "bin/eval-worker"
        policy = json.loads((installed / "share/policy_v1.json").read_text(encoding="utf-8"))
        sample_config = json.loads((installed / "conf/eval-worker.example.json").read_text(encoding="utf-8"))

        def invoke(arguments, task=None):
            wire = dumps(task, policy).encode("utf-8") if task is not None else b""
            return subprocess.run([str(binary), *map(str, arguments)], input=wire,
                                  cwd=outside, capture_output=True, timeout=10)

        version = invoke(["--version"])
        if version.returncode or version.stdout.decode().strip() != f"algoos-eval-worker {package['version']}":
            raise AssertionError("解包后的版本与归档清单不符")

        nodes = ("eval-01", "eval-02")
        configs = {}
        for node in nodes:
            (installed / "work" / node).mkdir()
            configs[node] = installed / "conf" / f"{node}.json"
            write_json(configs[node], dict(sample_config, node_id=node, work_dir=f"../work/{node}"))
            check = invoke(["check", configs[node]])
            if check.returncode:
                raise AssertionError(f"{node} 配置检查失败：{check.stderr.decode(errors='replace').strip()}")
            status = json.loads(check.stdout)
            if status != {"status": "ready", "worker_version": package["version"], "protocol_version": 1, "node_id": node}:
                raise AssertionError(f"{node} 配置检查结果不符")
            delivery["node_checks"].append(status)

        inputs = outside / "inputs"
        inputs.mkdir()
        copied = {}

        def copy_input(source):
            path = Path(source)
            if path not in copied:
                copied[path] = inputs / f"{len(copied):02d}_{path.name}"
                shutil.copy2(path, copied[path])
            return copied[path]

        first = None
        stable_fields = ("verdict", "reason", "exit_code", "signal", "wall_timeout", "out_bytes", "memory_source",
                         "stderr_tail", "short_circuited", "detail")
        for index, original in enumerate(originals):
            node = nodes[index % len(nodes)]
            prior = original["task"]
            attempt = prior["attempt"] + 1
            task = dict(prior, target_node_id=node, attempt=attempt, msg_id=f"delivery:{prior['msg_id']}",
                        judge_id=f"{prior['file_id']}:{prior['test_id']}:{attempt}",
                        emitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
            run_name = f"replay_{index + 1:02d}"
            arguments = ["run", configs[node], *(copy_input(path) for path in original["command"][3:6]), run_name]
            output_dir = installed / "work" / node / run_name
            proc = invoke(arguments, task)
            record = {"name": "installed_worker", "command": [str(binary), *map(str, arguments)],
                      "task": task, "cli_exit_code": proc.returncode, "cli_stderr": proc.stderr.decode(errors="replace")}
            cases.append(record)
            if proc.returncode:
                raise AssertionError(f"{run_name} 未生成结果：{record['cli_stderr'].strip()}")
            result = loads(proc.stdout, policy)
            actual = (output_dir / "stdout.out").read_bytes()
            record.update(result=result, captured_bytes=len(actual), actual_prefix=repr(actual[:64]))
            if any(result[key] != original["result"][key] for key in stable_fields):
                raise AssertionError(f"{run_name} 判定或诊断与本地回归不一致")
            for key in ("file_id", "correlation_id", "problem_id", "test_id", "sequence", "attempt", "judge_id"):
                if result[key] != task[key]:
                    raise AssertionError(f"{run_name} 身份不一致：{key}")
            if result["node_id"] != node or actual != (Path(original["command"][-1]) / "stdout.out").read_bytes():
                raise AssertionError(f"{run_name} 节点或实际输出不一致")
            if any((result[key] is None) != (original["result"][key] is None) for key in ("cpu_ms", "wall_ms", "mem_bytes")):
                raise AssertionError(f"{run_name} 测量值空缺语义不一致")
            record["status"] = "PASSED"
            if first is None:
                first = arguments, task, output_dir, sha256(output_dir / "stdout.out")

        def reject(name, arguments, diagnostic, task=None):
            proc = invoke(arguments, task)
            error = proc.stderr.decode(errors="replace")
            manifest["guards"].append({"name": name, "exit_code": proc.returncode, "stderr": error})
            if proc.returncode != 1 or proc.stdout or diagnostic not in error:
                raise AssertionError(f"Worker 未正确拒绝：{name}")

        arguments, task, output_dir, output_hash = first
        bad_config = installed / "conf/bad.json"
        write_json(bad_config, dict(sample_config, work_dir="../missing-work"))
        reject("worker_missing_work_dir", ["check", bad_config], "work_dir is not a writable directory")
        reject("worker_wrong_node", ["run", configs[nodes[1]], *arguments[2:-1], "wrong_node"], "target node mismatch", task)
        if (installed / "work" / nodes[1] / "wrong_node").exists():
            raise AssertionError("错节点任务不应创建工作目录")
        reject("worker_invalid_run_name", [*arguments[:-1], "invalid.name"], "invalid run name", task)
        reject("worker_existing_run_dir", arguments, "output directory already exists", task)
        if sha256(output_dir / "stdout.out") != output_hash:
            raise AssertionError("重复运行名覆盖了旧输出")
        delivery["replayed_cases"] = len(originals)
        delivery["guards"] = 4
    print(f"解包后两个配置均就绪，重放 {delivery['replayed_cases']} 个用例，4 项拒绝检查通过")
