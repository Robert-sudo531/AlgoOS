# AlgoOS

面向算法竞赛的轻量评测与任务调度实验平台。当前实现是 `reroll` 版本的本地评测守护进程：加载 `problems/`，监听 `submissions/`，调用 C++ 评测服务端，把结果写入 `result/result.tsv`。

## 快速开始

```bash
make -C judge/native
python3 test_pipeline.py
```

- C++ 源码与可执行文件同在 `judge/native/`：`eval_server.cpp`、`eval_server`、`eval_server_asan`。
- 守护进程启动时扫描 `problems/*/problem.json`，然后保持前台运行。
- 把提交源码命名为 `编号__题目ID.cpp`（如 `00000003__lazy_gold.cpp`）放入 `submissions/`。
- 编译产物为 `submissions/<stem>`，实际输出在 `submissions/<stem>__result/`。
- 每个提交一行追加到 `result/result.tsv`；`Ctrl-C` 停止守护进程。

## 当前目录

```text
项目根目录/
├── README.md                 # 唯一根级 Markdown，总索引
├── test_pipeline.py          # 前台评测入口
├── judge/
│   ├── problem.py            # problem.json 模型与派生 out_bytes
│   ├── pipeline.py           # 通用流程：编译、Socket、聚合、TSV、轮询
│   └── native/               # C++ 服务端源码、二进制、Makefile、协议说明
├── problems/                 # 题目包：problem.json + tests/*.in/*.out
├── submissions/              # 运行时提交与 __result/（内容不提交）
├── result/result.tsv         # 运行结果日志
├── docs/                     # 分类后的项目文档
│   ├── project/              # AI 协作规则、背景与排期
│   ├── design/               # 设计记录
│   ├── ops/                  # 部署、环境
│   ├── specs/                # 共同约定
│   ├── tasks/                # 系统/算法/应用任务清单
│   ├── records/              # 开发记录与历史备忘
│   ├── archive/              # 清理记录
│   └── templates/            # 课程报告模板
├── artifacts/                # 运行证据
└── work/                     # 临时文件（如存在，内容不提交）
```

## 文档索引

| 主题 | 位置 |
| --- | --- |
| AI 协作规则 | [docs/project/AGENTS.md](docs/project/AGENTS.md) |
| 项目背景与排期 | [docs/project/plan_revised.md](docs/project/plan_revised.md) |
| 设计记录 | [docs/design/CoreUML.md](docs/design/CoreUML.md) |
| C++ 评测服务端工作原理与 UML | [docs/design/eval_server.md](docs/design/eval_server.md) |
| 部署与使用 | [docs/ops/deployment.md](docs/ops/deployment.md) |
| 本机环境准备 | [docs/ops/local_setup.md](docs/ops/local_setup.md) |
| 字段、路径与结果约定 | [docs/specs/stage1_agreement.md](docs/specs/stage1_agreement.md) |
| 开发过程记录 | [docs/records/progress.md](docs/records/progress.md) |
| 系统任务清单 | [docs/tasks/system/](docs/tasks/system/) |
| 算法任务清单 | [docs/tasks/algorithm/](docs/tasks/algorithm/) |
| 应用与测试清单 | [docs/tasks/application/](docs/tasks/application/) |
| C++ 评测协议 | [judge/native/README.md](judge/native/README.md) |
| 2026-11-03 清理记录 | [docs/archive/2026-11-03_cleanup.md](docs/archive/2026-11-03_cleanup.md) |

> `AGENTS.md` 已归档到 `docs/project/AGENTS.md`；根目录不再自动加载 AI 协作规则，后续 AI 会话请从本索引进入。

## 评测口径

- `compare_mode` 只有 `common` / `special`；`special` 本期只保留枚举，返回 `SE`。
- `common`：统一换行，去行末空格/制表符和末尾空行后严格相等。
- `wall` 时限等于 `time_limit_ms`。
- 每个测试点的 `out_bytes` 由标准答案 trim 后字节数派生；输出超限归入 `WA`。
- 不使用 score；ACM 聚合：全部测试点 AC 才 AC，否则按顺序取第一个非 AC。
- 提交源码、可执行文件和结果目录都不提交；`submissions/` 内容被 `.gitignore` 忽略，`result/result.tsv` 作为运行日志保留。

## 验证

```bash
make -C judge/native
python3 test_pipeline.py
```

实际提交处理结果见 [result/result.tsv](result/result.tsv)，测试与服务端证据在 [artifacts/system/20261103_reroll_tests_ai/](artifacts/system/20261103_reroll_tests_ai/)。
