# AlgoOS 分布式重构进度与交接说明

> 日期：2026-11-16  
> 分支：`distributed-refactor`（从 `next_step` 创建）  
> 状态：**重构进行中，尚未提交，尚未完成端到端集群验收**  
> 依据：`docs/project/distributed_implementation_plan.md`、`docs/project/next_step.md`，并对照了 `midterm_report.md`、`docs/project/second_report_plan.md`、`docs/project/plan_revised.md`、`docs/specs/stage1_agreement.md`、`docs/tasks/algorithm/*`、`docs/tasks/application/*`、`docs/ops/deployment.md` 等文档。

---

## 1. 本轮目标

按两份目标文档做破坏性重构：

- 建立**协议层 / 控制面 / 编译集群 / 评测集群 / 特判集群 / 结果聚合 / 调度与实验**的完整边界；
- 实现统一 `classify()`、五层沙箱、取消广播、有序短路、特判回传、fallback、心跳、缓存、内容寻址、幂等去重；
- 实现 FCFS / SJF / Aging-SJF / Earliest-Finish / Cache-aware、离散事件模拟、模拟退火放置与指标；
- 整理目录，删除过渡脚本，旧单机链路迁入 `legacy/single_node/`。

**结论：核心代码骨架已落地，但端到端链路还没有真正跑通；旧目录尚未迁移，文档/部署/实验资产尚未补齐。**

---

## 2. 当前仓库状态

- 当前分支：`distributed-refactor`
- 尚未 commit，所有改动都在工作区：
  - 已修改：`.gitignore`、`protocol/policy_v1.json`、5 份原有 golden
  - 新增：`cpp/`、`python/`、`protocol/schemas/`、`protocol/fixtures/`、`migrations/`、`deploy/compose/`、`deploy/scripts/`、`pyproject.toml`、`uv.lock`、`build/`
- 旧目录仍在原处：`judge/`、`scripts/`、`tests/`、`web/`、`test_pipeline.py`、`deploy/n32/`
- `legacy/single_node/` 已创建，但**仍是空的**，旧代码还没有迁入
- `build/`、`__pycache__/`、`.ruff_cache/` 等尚未纳入 `.gitignore` 清理
- **没有执行 `git commit`，也没有推送**

---

## 3. 已完成并验证的部分

### 3.1 协议层（v1）

| 文件 | 内容 |
|---|---|
| `protocol/policy_v1.json` | 统一策略：六类消息、三种模式、编译 profile、六类 subject、对象路径、判定优先级、调度策略、短路窗口 |
| `python/algoos_protocol/models.py` | Pydantic 模型：`CompileTask`、`EvalTask`、`CompileResult`、`EvalResult`、`SpecialTask`、`SpecialResult`、`Cancel`、`Fallback`、`Heartbeat` |
| `python/algoos_protocol/codec.py` | 严格 JSON 解析、重复键/NUL/NaN/整数范围检查、规范化、哈希与时间工具 |
| `protocol/schemas/*.json` | 9 份 JSON Schema，由 Pydantic 模型导出 |
| `protocol/fixtures/*.json` | 6 份非法样例：重复键、未知类型、错误 judge_id、编译结果带信号、普通结果错误短路、NaN |
| `protocol/golden/*.json` | 10 份 golden：compile task/success/CE、eval task/result、special task/result、cancel、fallback、heartbeat |
| `protocol/generate_golden.py` | 可重复生成 golden 与非法 fixture，并做 Python 双向往返检查 |

**已做验证：**

```bash
uv run pytest -q
# 38 passed, 1 skipped
uv run ruff check python
# All checks passed!
```

- `test_protocol.py` 覆盖 golden 往返、JSON Schema 校验、非法 fixture 拒绝、未知字段保留、缺省 `status`、布尔冒充整数、六类 subject 存在性。
- **未覆盖**：C++ 端对 special/cancel/fallback 的全字段校验；C++ 目前只对 compile/eval 做严格解析。

### 3.2 C++ 执行面（Native Runner / Compile / Special）

| 模块 | 文件 | 内容 |
|---|---|---|
| JSON/IPC | `cpp/include/algoos/protocol/json.hpp`、`cpp/src/protocol/json.cpp` | 严格 JSON、重复键/UTF-8/越界整数/NaN 检查、长度前缀 Unix Socket 服务 |
| 哈希 | `cpp/include/algoos/protocol/digest.hpp`、`cpp/src/protocol/digest.cpp` | 自实现 SHA-256、canonical build key、`sha256:` 校验 |
| 消息 | `cpp/include/algoos/protocol/messages.hpp`、`cpp/src/protocol/messages.cpp` | eval/compile/special 任务解析、eval/special 结果构造 |
| 沙箱 | `cpp/include/algoos/sandbox/*`、`cpp/src/sandbox/sandbox.cpp` | cgroup v2（`memory.max`、`memory.swap.max=0`、`pids.max`、`cpu.max`、`memory.peak`、`memory.events`）、mount/PID/net/UTS/IPC namespace、seccomp 黑名单、rlimit（CORE/CPU/AS/FSIZE）、只读根 + tmpfs、进程组 kill、墙钟看门狗、PID 1 助手 + 真实程序 PID 2（保证 SIGSEGV/SIGXCPU 等语义） |
| 判定 | `cpp/src/sandbox/classify.cpp` | 单一判定表：SE → OOM → 墙钟 → CPU 信号 → 输出信号 → 崩溃信号 → 非零退出 → 内存峰值 → CPU 超时 → 输出超限 → COMPARE；支持 `OLE/OLE_TRUNCATED` 与合并 `WA/OUTPUT_LIMIT` |
| 比较 | `cpp/src/runner/comparator.cpp` | common 归一化、字节级比较、首个差异行、带 cap 输出读取 |
| 编译 | `cpp/src/worker/compile_service.cpp` | 固定 `g++ -std=c++17 -O2 -pipe`、rlimit/墙钟、编译诊断、build key、二进制产物 |
| 评测 | `cpp/src/worker/eval_service.cpp` | 正常评测 + `run_raw`（特判只运行不比较）、取消检查、输出/答案限制、cgroup 峰值 |
| 特判 | `cpp/src/worker/special_service.cpp` | 外部 checker：exit 0=AC、1=WA、其它=SE；沙箱与资源限制 |
| 取消 | `cpp/src/worker/cancel_registry.cpp` | submission/case 级取消、边界短路、在途进程组 kill |
| 服务 | `cpp/src/worker/server.cpp` | 有界队列、多 worker、`ping/cancel/stats` 内联处理、`busy` 背压 |
| 入口 | `cpp/apps/algoos-eval.cpp`、`algoos-compile.cpp`、`algoos-special.cpp` | 三个常驻 Unix Socket 服务 |

**已做验证：**

```bash
cmake -S cpp -B build/cpp -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/cpp --parallel 8
./build/cpp/algoos_unit_tests
# all unit tests passed
```

- 单元测试覆盖：SHA-256、严格 JSON（重复键/越界/NaN/中文）、common 比较、cap 读取、判定优先级（OOM/墙钟/崩溃/非零退出/内存峰值/CPU/输出）、真实沙箱（cgroup 峰值、namespace、seccomp 加载、SIGSEGV、墙钟 kill/TLE）。
- **注意**：最后一次给 eval 服务加 `run_raw` 后只重新编译，**未再复跑 ctest**；沙箱/判定测试是加 `run_raw` 之前通过的。
- **未验证**：seccomp 逐 syscall 黑名单的实际拦截；真实 cgroup OOM（`oom_kill`）触发；编译超时；special checker 的端到端路径。

### 3.3 Python 控制面

| 文件 | 内容 |
|---|---|
| `python/algoos_control/config.py` | 环境配置、节点配置加载 |
| `python/algoos_control/db.py` | PostgreSQL 连接、事务、SQL 迁移执行器、等待就绪 |
| `python/algoos_control/storage.py` | S3/MinIO 客户端、内容寻址 key、确定性 zip、`fetch_verified` |
| `python/algoos_control/broker.py` | NATS JetStream 连接、stream 创建/更新、发布、拉取消费者 |
| `python/algoos_control/outbox.py` | 事务 outbox、至少一次发布 |
| `python/algoos_control/submissions.py` | 提交、题目导入、查询、用户取消、cancel 事件 |
| `python/algoos_control/problem.py` | `problem.json` 校验、题包打包、checker 构建、逐测试点 input/answer 对象上传 |
| `python/algoos_control/compile_flow.py` | 编译结果消费、幂等落库、CE/SE 收敛、把计划物化为 READY cases |
| `python/algoos_control/scheduler.py` | 窗口化有序短路派发、节点存活筛选、缓存亲和分配、eval task 入 outbox |
| `python/algoos_control/aggregator.py` | eval 结果幂等落库、按 sequence 求首个非 AC、短路边界发布、fallback 转 eval result |
| `python/algoos_control/state.py` | 提交状态机、最终化、短路/取消状态 |
| `python/algoos_control/heartbeat.py` | 心跳入库、过期节点标记 |
| `python/algoos_control/service.py` | 控制面常驻：outbox、compile/eval/fallback/heartbeat 消费、调度循环、过期扫描 |
| `migrations/001_init.sql` | submissions、submission_cases、compile_jobs、eval_results、eval_nodes、processed_messages、outbox、cancel_events、experiments、experiment_metrics、problems |

**状态：代码已写，未连真实 PostgreSQL/NATS 运行过。**

### 3.4 节点代理与 Worker 桥接

| 文件 | 内容 |
|---|---|
| `python/algoos_nodes/ipc.py` | 长度前缀 JSON 客户端、节点 slot/load 计数 |
| `python/algoos_nodes/cache.py` | 本地内容寻址缓存、LRU 体积淘汰、缓存哈希列表 |
| `python/algoos_nodes/common.py` | NATS/Redis/对象存储/本地缓存/取消状态/心跳公共基类 |
| `python/algoos_nodes/messages.py` | eval/compile/special/fallback 结果构造 |
| `python/algoos_nodes/compile_agent.py` | 消费 compile task → 原生编译 → 上传 bin → 发布编译结果 |
| `python/algoos_nodes/eval_agent.py` | 消费 eval task → 缓存下载 → 原生评测 → 正常结果/特判任务；订阅 cancel 与 special result relay |
| `python/algoos_nodes/special_agent.py` | 消费 special task → 运行 checker → 活节点回传或 fallback |

**状态：代码已写，未连真实 NATS/MinIO/Redis 运行过。**

### 3.5 API / Web / CLI

| 文件 | 内容 |
|---|---|
| `python/algoos_api/app.py` | FastAPI：`POST/GET /api/v1/submissions`、取消、题目列表、节点、实验列表/详情、`POST /api/v1/experiments/simulation`、`/healthz`、静态页面；可选 Bearer Token；进程内提交限流 |
| `python/algoos_web/static/*` | 提交、提交记录、结果详情、节点心跳、调度模拟实验页面（原生 HTML/CSS/JS） |
| `python/algoos_cli/main.py` | `algoos api / control / compile-agent / eval-agent / special-agent / import-problems / submit / status / cancel / nodes / problems / experiment / doctor` |

**状态：代码已写，未做 API 集成测试；未连真实服务启动过。**

### 3.6 算法与实验

| 文件 | 内容 |
|---|---|
| `python/algoos_algorithms/policies.py` | FCFS、SJF、Aging-SJF、Earliest-Finish、Cache-aware；缓存亲和按 bin/bundle hash；节点/任务选择 |
| `python/algoos_algorithms/simulator.py` | 离散事件模拟：节点 slot、缓存副本、传输开销、五种策略 |
| `python/algoos_algorithms/metrics.py` | 平均/p50/p95/p99/最大等待、平均周转、makespan、吞吐、缓存命中率、Jain 公平、饥饿计数 |
| `python/algoos_algorithms/placement.py` | 模拟退火放置：max load、平均传输、负载不均衡、容量违约；固定 seed |
| `python/algoos_algorithms/workload.py` | mixed/short/heavy_tail/estimate_error/burst 合成负载；固定 seed 可复现 |

**已验证：** `test_algorithms.py` 覆盖 FCFS 顺序、SJF 选短、缓存亲和、Aging、模拟器确定性、SJF 降低平均等待、手算指标、SA 不劣于初始解。  
**未完成：** 没有真正的 `experiments/` 运行器、CSV/图表、真实集群对照实验、DOMjudge 对比。

### 3.7 构建与部署骨架

| 文件 | 内容 |
|---|---|
| `pyproject.toml` | Python 依赖、pytest、ruff、hatch 打包 |
| `uv.lock` | 已生成 |
| `cpp/CMakeLists.txt` | C++23、json-c、libseccomp、线程、ASan 选项、单元测试 |
| `deploy/compose/docker-compose.yml` | PostgreSQL / Redis / NATS / MinIO 服务 |
| `deploy/compose/.env.example`、`.env` | 镜像可配置；本机 `.env` 使用 daocloud 镜像源 |
| `deploy/scripts/local_cluster.sh` | 本机五角色启动脚本：基础设施 + 3 个原生 Worker + 4 个 Python Agent + control + API |

**已验证：** CMake 配置与构建成功；Python 依赖安装成功。  
**未完成：** `systemd/`、`cloud-init/`、`inventory/`、`kvm/` 目录为空；`local_cluster.sh` 尚未跑通。

---

## 4. 当前阻塞与未验证项（重要）

### 4.1 端到端链路尚未跑通

以下流程**没有任何一次真实运行证据**：

```text
API 提交 → PostgreSQL/outbox → NATS compile task → compile agent
→ 原生编译 → MinIO 上传 → compile result → submission_cases
→ scheduler → NATS eval task → eval agent → 原生评测
→ eval result → aggregator → 最终状态 → API 查询
```

因此不能声称“分布式集群已实现并验收”，只能声称“代码骨架已落地，待联调”。

### 4.2 本机服务启动被网络阻断

- Docker Hub 直连超时。
- 通过 `docker.m.daocloud.io` 已成功拉取：
  - `postgres:17-alpine`
  - `redis:7.4-alpine`
  - `nats:2.10-alpine`
- **MinIO 镜像被 daocloud 源拒绝（denied）**；`dl.min.io` 返回 410/403；GitHub 直连超时。
- 原计划用 `moto[server]` 作为本机 S3 兼容替代，但安装命令随本次暂停被中止，尚未安装/验证。
- `deploy/scripts/local_cluster.sh` 当前仍尝试 `docker compose up -d` 全部服务，会卡在 MinIO；需要改为“Postgres/Redis/NATS 用容器 + S3 用 moto 或可用 MinIO”。
- 上次启动命令在后台被停止；`docker ps` 当前没有 AlgoOS 容器，端口 5432/6379/4222/9000 未监听。

### 4.3 旧代码尚未迁移/删除

目标结构要求“只保留一个分布式主线，旧单机链路移入 `legacy/single_node/`”。当前：

- `judge/`、`scripts/`、`tests/`、`web/`、`test_pipeline.py`、`deploy/n32/` 仍在根目录；
- `legacy/single_node/` 为空；
- 根 `README.md` 仍描述旧的 `test_pipeline.py` 单机守护进程；
- 旧 `protocol/codec_v1.py`、`protocol/codec_v1.hpp`、`protocol/contract_check.cpp` 仍与新协议并存。

### 4.4 工程清理未做

- `build/`、`python/**/__pycache__/`、`.ruff_cache/` 尚未加入 `.gitignore`；
- 没有提交，没有 PR；
- 没有新的架构文档、部署文档、实验说明；
- `docs/records/progress.md` 未追加本轮记录。

### 4.5 功能缺口（对照两份目标文档）

| 目标 | 状态 |
|---|---|
| API 网关（鉴权/限流/提交/查询/取消/实验） | 代码已写，未联调；限流是进程内，非 Redis 全局限流 |
| Redis 取消集/心跳/会话 | 心跳与取消快速路径已写，未联调 |
| PostgreSQL 元数据库 | 表结构已写，未执行迁移；未验证事务/幂等/JSONB |
| MinIO 对象存储 | 代码已写，未连真实 S3；本机 MinIO 不可用 |
| NATS JetStream 六类消息 | 代码已写，未联调 |
| 编译集群 | 单编译节点代码已写，无常驻多节点验证；无编译 CPU/内存上限独立测试 |
| 评测集群 | 两逻辑节点代码已写，无跨机/真实并发/缓存命中证据 |
| 特判集群 | checker 运行、回传、fallback 代码已写，未联调 |
| 取消 + 有序短路 | 代码已写，未联调；窗口/边界正确性未用真实消息验证 |
| 五层沙箱 | 本机沙箱单测已过；真实 cgroup OOM、seccomp 逐 syscall、编译沙箱未验证 |
| 调度策略/模拟/SA | 算法单测已过；未接真实集群，未产出实验数据/图表 |
| KVM/QEMU 五 VM | 只有目录，无脚本/无实际部署 |
| systemd/cloud-init/inventory | 目录为空 |
| 实验矩阵/DOMjudge 对比/报告 | 未开始 |
| 故障注入（节点下线、任务迁移、取消传播延迟） | 未开始 |
| 性能/尾延迟/重复实验 | 未开始 |

### 4.6 已知代码风险

1. **C++ 沙箱**：seccomp 是“黑名单”而非严格 allowlist；只读根、tmpfs、namespace 在本机 root 下验证过，但不同宿主/非 root 行为未验证。
2. **C++ MLE/OLE**：`classify` 规则已实现，真实 cgroup OOM 未触发；`RLIMIT_AS` 超限仍可能表现成 RE。
3. **C++ 编译**：`run_bounded_process` 使用 rlimit，但编译 CPU/内存上限没有独立测试；编译超时只写了代码。
4. **Python 控制面**：SQL/JSONB 参数、UUID 文本转换、`FOR UPDATE` 锁范围、事务内调用 `publish_cancel` 都未在真实 PG 上跑过。
5. **调度器**：`scheduler.py` 用 `time.monotonic()` 作为毫秒，跨进程不具可比性；`ReadyCase.arrival_ms=0` 使 aging 在真实集群中没有实际等待时间输入；需要改成 `submitted_at`/`dispatched_at` 的绝对时间。
6. **聚合器**：短路边界、重复结果、迟到结果、用户取消与短路同时发生的组合未测；`recompute_status` 的最终状态可能与目标状态机不完全一致。
7. **API**：`/api/v1/experiments/simulation` 的 JSONB 写入、`list_experiments` 的 `summary` 类型未验证；无正式 OpenAPI 契约测试。
8. **Web**：页面只做轮询，未做错误/空状态完整处理；未做截图/浏览器验证。
9. **对象存储**：`put_output` 同时写 `out/<file>/...` 和 `outobj/<hex>.out`，是当前特判回传的临时设计；正式方案应统一 TTL/清理策略。
10. **local_cluster.sh**：使用 `uv run` 后台进程，未验证 PATH/工作目录/信号回收；MinIO 未解决前不能启动。

---

## 5. 建议的下一步顺序

1. **先打通本机 S3**：安装 `moto[server]`，修改 `local_cluster.sh`：Postgres/Redis/NATS 用容器，S3 用 moto；或找到可用的 MinIO 镜像/二进制。
2. **跑 DB 迁移 + 健康检查**：`uv run algoos doctor`、`uv run algoos import-problems`。
3. **跑最小端到端**：提交 `examples/a_plus_b.cpp`，验证 AC；再跑 CE/WA/RE/TLE/SE。
4. **补特判样例与取消/短路**：增加 special problem + checker；验证 special-result relay、fallback、用户取消、有序短路边界。
5. **两逻辑节点/缓存/重复投递**：验证 eval-01/eval-02 分发、缓存命中、NATS 重投、`judge_id` 去重。
6. **补集成测试**：`python/tests/integration/`，使用 pytest fixture 启停服务；覆盖 AC/WA/CE/RE/TLE/SE、取消、特判、fallback、重复结果。
7. **补部署资产**：`deploy/systemd/`、`deploy/cloud-init/`、`deploy/inventory/`、`deploy/kvm/`。
8. **补实验运行器**：`experiments/runner.py`、`experiments/analysis/`、固定 workload、CSV/JSON/图表、重复实验；明确模拟 vs 真实。
9. **迁移旧代码**：`git mv` 旧 `judge/`、`scripts/`、`tests/`、`web/`、`test_pipeline.py`、`deploy/n32/` 到 `legacy/single_node/`；删除无用过渡文件。
10. **重写 README/文档**：新架构、启动、API、部署、实验、证据规范；追加 `docs/records/progress.md`。
11. **最终验收**：CMake/ctest + pytest + ruff + 端到端 + 故障注入；再提交/PR。

---

## 6. 复现命令（当前可用部分）

```bash
# C++ 构建与单元测试
export PATH="$HOME/.local/bin:$PATH"
cmake -S cpp -B build/cpp -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/cpp --parallel 8
./build/cpp/algoos_unit_tests

# Python 依赖、测试、静态检查
uv sync
uv run pytest -q
uv run ruff check python

# 协议 golden/schema 重新生成
uv run python protocol/generate_golden.py
uv run python -m algoos_protocol.export_schemas

# 本机集群（当前会被 MinIO 阻塞，需先按第 5 节修复）
deploy/scripts/local_cluster.sh start
```

---

## 7. 交接注意事项

- **不要把当前状态写成“分布式集群已完成”**。准确表述是：*协议、执行内核、控制面/节点代理、调度算法和前端骨架已落地；本机端到端联调尚未通过。*
- **不要把模拟数据当真实实验结果**。`algoos_algorithms` 的模拟器输出必须标注 `simulation`。
- **不要声称 MLE/OLE/cgroup/seccomp 已完成生产验证**。当前只有合成判定单测和本机 root 沙箱冒烟。
- **旧单机代码仍在根目录**，在迁移完成前，README 的旧命令仍然可用；两套实现并存是暂时的。
- 下一步最值得先做的是 **S3 替代 + DB 迁移 + 最小 E2E**，而不是继续加功能；否则无法判断已有代码是否正确。
