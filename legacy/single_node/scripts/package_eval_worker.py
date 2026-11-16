"""从本地构建的 eval-worker 生成可独立解包运行的发布归档。"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
from tempfile import TemporaryDirectory


def package(binary, output_dir):
    root = Path(__file__).resolve().parents[1]
    binary = binary.resolve(strict=True)
    version_line = subprocess.check_output([str(binary), "--version"], text=True, timeout=10).strip()
    match = re.fullmatch(r"algoos-eval-worker ([A-Za-z0-9.-]+)", version_line)
    if not match:
        raise ValueError("unexpected eval-worker version string")
    version = match.group(1)
    links = subprocess.check_output(["ldd", str(binary)], text=True, timeout=10)
    if "not found" in links:
        raise RuntimeError("eval-worker has missing runtime libraries")
    libraries = sorted({Path(line.split()[0]).name for line in links.splitlines()
                        if line.strip() and not line.lstrip().startswith("linux-vdso")})
    system, machine = platform.system().lower(), platform.machine()
    name = f"algoos-eval-worker-{version}-{system}-{machine}"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{name}.tar.gz"
    with TemporaryDirectory(prefix="algoos_worker_package_") as staging:
        package_dir = Path(staging) / name
        files = {
            "bin/eval-worker": binary,
            "conf/eval-worker.example.json": root / "judge/native/eval-worker.example.json",
            "share/policy_v1.json": root / "protocol/policy_v1.json",
        }
        digests = {}
        for relative, source in files.items():
            target = package_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            digests[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        (package_dir / "work").mkdir()
        libc, libc_version = platform.libc_ver()
        manifest = {
            "role": "eval-worker", "version": version, "protocol_version": 1,
            "platform": {"system": system, "architecture": machine, "libc": libc, "libc_version": libc_version},
            "scope": "local_one_shot", "runtime_libraries": libraries, "sha256": digests,
        }
        (package_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        with tarfile.open(archive, "x:gz") as output:
            output.add(package_dir, arcname=name)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(package(args.binary, args.output_dir).resolve())


if __name__ == "__main__":
    main()
