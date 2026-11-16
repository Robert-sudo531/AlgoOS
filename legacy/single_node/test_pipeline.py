"""前台常驻评测入口：加载 problems/，监听 submissions/，写 result/result.tsv。"""

from __future__ import annotations

import argparse
from pathlib import Path

from judge.pipeline import Pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        default="judge/native/eval_server",
        help="C++ 评测服务端路径（相对项目根目录或绝对路径）",
    )
    parser.add_argument("--port", type=int, default=0, help="0 表示自动选空闲端口")
    parser.add_argument("--workers", type=int, default=4, help="评测 worker 数量")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    server_bin = Path(args.server)
    if not server_bin.is_absolute():
        server_bin = root / server_bin

    pipeline = Pipeline(
        problems_dir=root / "problems",
        submissions_dir=root / "submissions",
        result_dir=root / "result",
        server_bin=server_bin,
        workers=args.workers,
        port=args.port,
    )
    pipeline.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
