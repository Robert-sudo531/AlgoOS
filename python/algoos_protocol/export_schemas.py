"""Export JSON Schema files from the Pydantic v2 message models.

Run ``python -m algoos_protocol.export_schemas`` from ``python/`` to refresh
``protocol/schemas``.  The schemas are derived artifacts; the models are the
source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import (
    Cancel,
    CompileResult,
    CompileTask,
    EvalResult,
    EvalTask,
    Fallback,
    Heartbeat,
    SpecialResult,
    SpecialTask,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "protocol" / "schemas"

MODELS = {
    "compile_task": CompileTask,
    "compile_result": CompileResult,
    "eval_task": EvalTask,
    "eval_result": EvalResult,
    "special_task": SpecialTask,
    "special_result": SpecialResult,
    "cancel": Cancel,
    "fallback": Fallback,
    "heartbeat": Heartbeat,
}


def export(directory: Path = SCHEMA_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                  "$id": f"https://algoos.local/schemas/{name}.json", **schema}
        path = directory / f"{name}.json"
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for path in export():
        print(path.relative_to(ROOT))
