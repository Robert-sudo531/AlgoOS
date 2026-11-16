# 文件结构与命名规范

> 本文件是仓库结构的唯一约定。新增、移动或删除文件前先读这里；
> 结构与规范不一致时，以本文件为准并同步更新本文件。

## 1. 顶层结构

```text
AlgoOS/
├── README.md                       项目总览、快速开始、结构索引
├── deployment.md                   部署与自测指南
├── pyproject.toml                  Python 依赖、脚本入口、ruff/pytest 配置
├── uv.lock                         锁文件
├── cpp/                            C++23 执行内核与 Worker
│   ├── CMakeLists.txt
│   ├── apps/
│   │   ├── compile_main.cpp        编译 Worker 入口（产物 algoos-compile）
│   │   ├── eval_main.cpp           评测 Worker 入口（产物 algoos-eval）
│   │   └── special_main.cpp        特判 Worker 入口（产物 algoos-special）
│   ├── include/algoos/             公共头文件
│   │   ├── protocol/               JSON、消息、摘要
│   │   ├── runner/                 比较器
│   │   ├── sandbox/                沙箱与判定
│   │   └── worker/                 服务、取消、编译/评测/特判
│   ├── src/                        实现，目录与 include 对齐
│   └── tests/core_tests.cpp        核心测试（产物 algoos-core-tests）
├── python/                         Python 主线
│   ├── algoos_protocol/            协议模型、编码、Schema 导出
│   ├── algoos_control/             控制面、调度、聚合、状态机、存储、迁移
│   ├── algoos_nodes/               compile/eval/special Agent、缓存、原生 IPC
│   ├── algoos_api/                 FastAPI 网关
│   ├── algoos_cli/                 `algoos` CLI
│   ├── algoos_web/static/          Web 静态页面
│   ├── algoos_algorithms/          调度策略、模拟器、指标、SA 放置
│   ├── algoos_testing/             真实本机集群测试夹具
│   └── tests/
│       ├── test_*.py               单元测试
│       └── integration/            真实服务集成测试
├── protocol/
│   ├── policy_v1.json              协议/主题/判定/调度策略唯一来源
│   ├── schemas/                    Pydantic 导出的 JSON Schema
│   ├── golden/                     合法 golden 消息
│   ├── fixtures/                   非法消息 fixture
│   └── generate_golden.py          golden/fixture 生成器
├── migrations/                     PostgreSQL 迁移，按 `NNN_description.sql` 排序
├── deploy/
│   ├── compose/                    PostgreSQL/Redis/NATS/MinIO
│   ├── systemd/                    API/控制面/Worker/Agent 单元
│   ├── cloud-init/                 VM cloud-init
│   ├── inventory/                  五角色节点清单
│   ├── kvm/                        五 VM 定义与 provision
│   └── scripts/                    集群启停、健康检查、节点安装
├── experiments/
│   ├── runner/                     真实本机集群固定轨迹实验
│   ├── simulator/                  离散事件模拟
│   ├── placement/                  模拟退火副本放置
│   ├── analysis/                   Markdown 报告
│   ├── profiles/                   固定策略/负载/SA 参数
│   └── workloads/                  固定提交轨迹
├── problems/                       题目包，`<problem_id>/problem.json + tests/NN.in|out`
├── examples/                       验证程序，`snake_case.cpp`
├── scripts/                        verification 脚本
│   ├── verify_special_flow.py      特判/取消/有序短路
│   └── verify_reliability_flow.py  缓存/重复/重启/重派/fallback
├── artifacts/
│   ├── verification/               本机验收证据
│   └── experiments/                实验输出
└── docs/
    ├── README.md                   文档分区索引
    ├── requirements/               目标与需求（总案、next_step）
    ├── design/                     当前架构设计
    ├── operations/                 当前部署与运维
    ├── status/                     当前实现状态与交接
    ├── development/                结构规范
    ├── records/                    开发记录与历史笔记
    ├── reports/                    课程报告、PPT、模板
    └── archive/                    冻结的历史文档（旧单机、课程过程）
```

## 2. 命名规范

### 2.1 目录

- 全部小写，使用名词；多个单词用 `_`：`algoos_control`、`cloud-init` 是外部约定例外。
- 不建立 `misc/`、`temp/`、`new/`、`old/`、`stageN/`、`n1_n2/` 这类临时目录。
- 历史内容只进 `docs/archive/<topic>/`；代码不保留“旧版本副本”。
- 运行产物只进 `/work`、`/tmp` 或 `artifacts/`；不入库的内容必须写入 `.gitignore`。

### 2.2 Python

- 包：`algoos_<domain>`；模块：`snake_case`，名字必须说明职责。
- 禁止 `common.py`、`utils.py`、`misc.py`、`helpers.py`、`text.py`、`service.py`、
  `state.py`、`ipc.py` 这类泛名。已有文件按职责重命名：
  - `agent_base.py`：Agent 公共基类；
  - `native_client.py`：原生 Worker Unix Socket 客户端；
  - `normalization.py`：输出归一化；
  - `control_service.py`：控制面常驻循环；
  - `submission_state.py`：提交状态机；
  - `local_cluster.py`：真实本机集群测试夹具。
- 测试：`test_<subject>.py`；集成测试放 `python/tests/integration/`。
- 验证脚本：`scripts/verify_<flow>.py`，文件名描述被验证的流程，不使用 `step4/step5`。

### 2.3 C++

- 源文件/头文件：`snake_case.cpp`、`snake_case.hpp`；类名 `PascalCase`。
- 可执行文件：`algoos-<role>`（kebab-case），例如 `algoos-compile`、`algoos-eval`、
  `algoos-special`；核心测试可执行文件 `algoos-core-tests`。
- 入口源文件：`compile_main.cpp`、`eval_main.cpp`、`special_main.cpp`；CMake 负责
  映射到最终可执行文件名。
- 命名避免：`server.cpp`、`utils.cpp`、`misc.cpp`。当前 Unix Socket 服务端为
  `socket_server.cpp`。

### 2.4 Shell

- 动作脚本：`verb_object.sh`，例如 `check_health.sh`、`install_node.sh`、
  `reset_local_cluster.sh`。
- `deploy/scripts/local_cluster.sh` 是集群启停的唯一操作入口（`start|stop|status|logs|clean`），
  作为多动作入口保留名词命名，属于本规范唯一例外。

### 2.5 文档

- 文件名：小写 `snake_case.md`；`README.md` 是通用例外。
- 分区：
  - `docs/requirements/`：目标、计划、验收标准；
  - `docs/design/`：当前架构；
  - `docs/operations/`：当前部署/运维；
  - `docs/status/`：实现状态与交接；
  - `docs/development/`：命名与结构规范；
  - `docs/records/`：开发记录、笔记；
  - `docs/reports/`：课程报告、PPT、模板；
  - `docs/archive/`：冻结历史，不再更新。
- 文档标题（一级标题）写清“本文是什么”，不使用 `n1/n2/stage`、`TODO`、`杂项` 作为标题。
- 归档文档保留原文，但链接统一指向新路径；已删除的旧代码链接改成纯文本说明。

### 2.6 证据与实验

- 目录：`YYYYMMDDTHHMMSSZ_<scope>`，UTC 时间；scope 用小写 `snake_case`。
- 验收证据：`artifacts/verification/<timestamp>_<flow>/`，必须含 `manifest.json`。
- 实验输出：`artifacts/experiments/<timestamp>_<kind>_<strategy>/`，必须含 `manifest.json`，
  且 `data_source=cluster|simulation` 是唯一口径。
- 禁止只用随机 hash 作为目录名（如 `n1_ac_9c1bf7fe479d`）；禁止把一次运行的临时
  `refactor_step4_<hash>` 直接留在 `artifacts/system/`。
- 当前保留的证据：
  - `artifacts/verification/20261116T082019Z_baseline_smoke/`
  - `artifacts/verification/20261116T123748Z_special_flow/`
  - `artifacts/verification/20261116T123819Z_reliability_flow/`
  - `artifacts/verification/20261116T123627Z_minio_integration/`
  - `artifacts/experiments/20261116T122321Z_placement_sa/`
  - `artifacts/experiments/20261116T122321Z_simulation_mixed/`
  - `artifacts/experiments/20261116T122302Z_cluster_cache_aware/`

### 2.7 题目与样例

- 题目目录：`problems/<problem_id>/`，`problem_id` 小写 `snake_case`。
- 测试点：`tests/NN.in`、`tests/NN.out`，编号从 01 开始；不再混用 `1.in` 与 `01.in`。
- 样例源码：`examples/<name>.cpp`，名字表达用例目的（`compile_error.cpp`、
  `time_limit.cpp`），不使用 `a.cpp`、`test2.cpp`。

## 3. 删除与清理规则

- 测试打包 `*.zip`、构建产物、缓存、`work/`、`__pycache__`、`.pytest_cache`、
  `.ruff_cache` 不入库；`.gitignore` 已覆盖。
- 一次任务结束后删除 `/work`、临时 zip 和失败/重复 evidence；只保留通过且被文档引用的证据。
- 旧实现不保留双份代码；删除前确认没有构建/测试/文档引用，删除后在
  [实现状态](../status/implementation_status.md) 记录。
- 任何目录移动/重命名后运行 `python -m pytest -q`、`ruff check` 和本地链接检查。

## 4. 新增文件的放置决策

1. 是运行时代码？放 `python/algoos_*` 或 `cpp/src|apps`。
2. 是构建/部署资产？放 `deploy/` 对应子目录。
3. 是验证场景？放 `scripts/verify_<flow>.py`；可重复测试放 `python/tests/`。
4. 是实验？放 `experiments/<kind>/`，输出放 `artifacts/experiments/`。
5. 是文档？按第 2.5 节分区；历史内容进 `docs/archive/`。
6. 是证据？放 `artifacts/verification/` 或 `artifacts/experiments/`，必须带 manifest。
