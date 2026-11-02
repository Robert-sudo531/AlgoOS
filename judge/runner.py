# import subprocess


# def run_program(executable_path, input_text, timeout_seconds=5):
#     result = subprocess.run(
#         [str(executable_path)],
#         input=input_text,
#         capture_output=True,
#         text=True,
#         timeout=timeout_seconds,
#     )

#     return {
#         "exit_code": result.returncode,
#         "stdout": result.stdout,
#         "stderr": result.stderr,
#     }
import subprocess


def _as_text(output):
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def run_program(executable_path, input_text, timeout_seconds=5):
    try:
        result = subprocess.run(
            [str(executable_path)],
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "status": "RUN_TIMEOUT",
            "exit_code": None,
            "stdout": _as_text(error.stdout),
            "stderr": _as_text(error.stderr),
            "error_message": (
                f"运行超过 {timeout_seconds} 秒实际经过时间保护"
            ),
        }

    except OSError as error:
        return {
            "status": "SYSTEM_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "error_message": str(error),
        }

    return {
        "status": "FINISHED",
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "error_message": "",
    }
