import subprocess


def run_program(executable_path, input_text, timeout_seconds=5):
    result = subprocess.run(
        [str(executable_path)],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )

    return {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }