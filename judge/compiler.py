import subprocess
from pathlib import Path


def compile_cpp(source_path, build_dir):
    source_path = Path(source_path)
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=False)

    executable_path = build_dir / "program"

    result = subprocess.run(
        [
            "g++",
            "-std=c++17",
            str(source_path),
            "-o",
            str(executable_path),
        ],
        capture_output=True,
        text=True,
    )

    success = result.returncode == 0

    return {
        "success": success,
        "exit_code": result.returncode,
        "executable_path": str(executable_path) if success else None,
        "stderr": result.stderr,
    }