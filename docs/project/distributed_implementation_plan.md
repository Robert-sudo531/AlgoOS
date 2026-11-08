# AlgoOS 分布式集群算法评测平台：完整实施总案

> 版本：v1.0（实施设计稿）  
> 依据：[中期报告](../../midterm_report.md)、[完整 UML 与接口规范](../../next_step.md)及当前单机评测内核。  
> 定位：本文件是下一阶段的**代码分案、重构边界、部署方案和实验方案**。`next_step.md` 是目标 UML/接口的权威定义；本文件将其拆解为可实现、可验收的工程工作包。

---

## 0. 项目定位、现实意义与边界

### 0.1 推荐项目名称

**面向资源受限环境的分布式集群部署与评测调度优化平台（AlgoOS）**

### 0.2 可直接用于报告的研究动机

云计算、边缘计算和校内私有云都面临同一个基本问题：面对突发、异构且资源受限的任务负载，如何把任务可靠地分配到多个执行节点，并在性能、资源利用、数据传输和故障恢复之间取得可度量的平衡。仅把单机程序复制到多个虚拟机，并不自然等于高效集群：控制面与执行面会产生状态一致性问题，任务和结果会重复投递，数据会在节点间反复传输，局部缓存会影响实际完成时间，节点下线还会使异步结果丢失或重复。

本项目选择“在线评测”作为分布式集群研究的实验载体，而不是把 OJ 页面本身当作唯一目标。评测任务具有提交—编译—多测试点执行—结果汇聚的明确依赖关系，既包含 CPU/内存密集型运行，又包含二进制、测试包和输出文件的存储与传输；同时 verdict 为任务完成提供了可精确验证的正确性标准。因此，它能在不依赖企业真实敏感负载的情况下，稳定复现队列拥塞、节点异构、缓存亲和、取消传播、故障转移和幂等收敛等典型分布式问题。

项目以 KVM/QEMU 在一台服务器上构造可控集群：每个 VM 模拟独立执行节点，统一镜像、资源配额和网络拓扑，并可通过快照反复恢复实验初态。这种方式降低了多物理机实验的成本，却仍可研究消息队列、对象存储、心跳、远程执行和调度策略在逻辑分布式环境中的行为。通过 C++ Worker、Python 控制面、对象存储和持久消息队列的分层实现，平台能够把“部署是否正确”“优化是否有效”转化为可追踪的状态、事件和指标。

项目的现实与研究意义在于：

1. 提供一套适合资源有限实验室或教学团队的轻量集群部署范式，验证控制面、执行面、存储和队列如何协同，而非依赖单机串行评测；
2. 研究节点数量、资源配额、缓存局部性、任务粒度和失败重试如何共同影响吞吐、尾延迟、负载均衡和资源利用率；
3. 将在线贪心调度与离线预放置/模拟退火置于同一可复现实验环境，比较“即时决策”和“已知批量任务优化”的适用条件；
4. 以 DOMjudge 作为成熟工程基线，识别自建分布式架构在正确性、开销和可观测性上的边界，而不是笼统宣称性能更优；
5. 形成可迁移的方法：任务描述、幂等事件、数据本地性、健康检查和资源隔离的设计经验也适用于批处理、CI 执行器、教学云实验等任务集群。

### 0.3 可检验研究问题

- **RQ1：** 与单机集中式执行相比，增加逻辑评测节点在何种到达强度和任务分布下能改善吞吐、p95 周转时间和资源利用率，何时会被通信、存储和协调开销抵消？
- **RQ2：** FCFS、短任务优先、老化短任务优先、最早完成时间和缓存亲和贪心堆调度，对平均等待时间、尾延迟、公平性、缓存命中率和跨节点数据传输有何影响？
- **RQ3：** 对于已知批量任务，贪心预放置与“贪心初解 + 模拟退火”能否降低 makespan、负载方差和跨节点传输量；这种收益能否在真实 VM 回放中复现？
- **RQ4：** 节点下线、重复投递、特判异步回传和取消广播能否在不丢失最终结果的前提下正确收敛；恢复代价是多少？
- **RQ5：** 在相同题包、编译器、资源限额和虚拟硬件配额下，AlgoOS 与 DOMjudge 的 verdict 一致性、端到端开销和尾延迟有何差异？

### 0.4 不应夸大的结论

- 多台 KVM 虚拟机仍位于一台物理服务器，验证的是**逻辑分布式架构、调度和故障协议**，不是跨物理机高可用能力。
- 控制节点上的单实例 PostgreSQL、MinIO、NATS 在首版是单点；主从、跨机复制只作为后续扩展，不可声称已实现高可用。
- cgroup、namespace、seccomp 的隔离效果必须在实际评测 VM 上逐项验证；未验证前只能写“已接入/待验证”，不能写“完全安全”。
- DOMjudge 是成熟基线，不是需要被“全面超越”的对象；比较结论只对已控制变量的版本、题包和工作负载成立。

---

## 1. 目标架构与阶段化实现原则

`next_step.md` 中的网关、元数据库、对象存储、六类消息、编译集群、评测集群、特判集群、取消、回退和聚合器全部保留为目标架构。实现时按下面的层次推进，避免为了“有微服务”而复制状态。

```text
浏览器 / 压测客户端
        │ HTTPS / HTTP
        ▼
Python API Gateway ── PostgreSQL（提交、状态、结果、实验记录）
        │         └── Redis（心跳、取消集、会话、短期限流）
        ├── MinIO（source/bin/tests/out/log，内容寻址）
        └── NATS JetStream（持久消息与消费者确认）
                │
    ┌───────────┼─────────────────────────────────────────────┐
    ▼           ▼                    ▼                         ▼
C++ Compile  C++ Eval Worker × N  C++ Special Worker × N  Python/C++ Aggregator
    │           │                    │                         │
  bin 上传    sandbox/classify      checker/relay             最终收敛
```

### 1.1 控制面与数据面

| 层 | 技术与职责 | 不负责的事 |
|---|---|---|
| Python 控制面 | FastAPI、认证、提交、查询、DB 事务、对象元数据、实验启动 | 不 fork 用户程序，不做资源类 verdict |
| C++ 数据面 | 编译、缓存、沙箱、运行、`classify()`、特判执行、高频消息消费 | 不直接提供用户 HTTP 页面，不保存业务真相 |
| PostgreSQL | 提交状态、测试点结果、实验配置、幂等记录 | 不保存源码、可执行文件、大输出 |
| MinIO | 源码、二进制、测试包、输出、编译日志 | 不承担查询索引或队列 |
| Redis | 节点心跳、取消集合、短期 session | 不作为最终 verdict 的唯一事实来源 |
| NATS JetStream | 至少一次投递、持久 stream、消费确认、重投 | 不保证业务层 exactly-once |

### 1.2 最终消息流

`next_step.md` 中“topic”是语义名；实现采用以下 NATS subject。NATS 负载本身就是一条 JSON 消息，**不添加 4 字节 TCP 长度前缀**；该长度前缀只保留给旧本地 `eval_server` 的迁移适配层。

| 语义 topic | NATS subject | 生产者 | 消费者 |
|---|---|---|---|
| compile-topic | `algoos.compile.task` | Gateway / CompileScheduler | Compile Worker |
| eval-topic 任务 | `algoos.eval.task` | Compile Worker / EvalScheduler | Eval Worker |
| eval-topic 结果 | `algoos.eval.result` | Eval Worker、Relay、Fallback | ResultAggregator |
| special-topic | `algoos.special.task` | Eval Worker | Special Worker |
| special-result-topic | `algoos.special.result.<node_id>` | SpecialReplyRouter | 对应 Eval Worker Relay |
| cancel-topic | `algoos.cancel` | Eval Worker、Relay、Gateway | 所有 Eval Worker、Scheduler |
| fallback-topic | `algoos.special.fallback` | SpecialReplyRouter | FallbackConsumer |

`special.result.<node_id>` 同时保留载荷中的 `target_eval_node`，因此满足 UML 的“目标过滤”语义，且避免每台评测机消费并丢弃所有其他节点的特判结果。

### 1.3 两种评测模式

| 模式 | 目的 | 取消规则 |
|---|---|---|
| `all_cases` | 正确性基线、DOMjudge 性能对比 | 所有测试点运行完成，不因 WA/TLE 等短路 |
| `ordered_short_circuit` | 真实教学反馈和取消机制实验 | 测试点按稳定序号滑动窗口派发；只有已确认所有更小序号为 AC 后，某个失败才触发取消 |

不能采用“任意一个并发测试点先失败就取消”的实现：它会把最终“首个非 AC”从测试点顺序错误地变成完成时间顺序。

---

## 2. 破坏性重构后的目标目录

完成迁移后，根目录应只保留一个分布式主线；旧单机链路作为可运行基线移入 `legacy/`，不再作为生产入口。

```text
AlgoOS/
├── CMakeLists.txt
├── CMakePresets.json
├── pyproject.toml
├── uv.lock
├── README.md
├── midterm_report.md
├── next_step.md
├── cpp/
│   ├── include/algoos/
│   │   ├── protocol/       # 版本化 DTO、Verdict、Reason、序列化
│   │   ├── runner/         # Evidence、classify、compare、process runner
│   │   ├── sandbox/        # SandboxManager 与 rlimit/isolate/cgroup 适配器
│   │   ├── cache/          # bin/tests 的哈希缓存与 LRU 元数据
│   │   ├── broker/         # NATS JetStream 消费、确认、重投、幂等
│   │   ├── worker/         # Compile/Eval/Special/Relay/Fallback
│   │   └── scheduler/      # 在线堆调度与离线预放置/退火
│   ├── src/
│   ├── apps/
│   │   ├── compile_worker_main.cpp
│   │   ├── eval_worker_main.cpp
│   │   ├── special_worker_main.cpp
│   │   ├── scheduler_main.cpp
│   │   └── benchmark_replay_main.cpp
│   └── tests/
├── python/
│   ├── algoos_api/         # FastAPI 路由、认证、请求响应模型
│   ├── algoos_control/     # DB 仓储、NATS 发布、MinIO、聚合器、心跳
│   ├── algoos_cli/         # 初始化、提交回放、故障注入、结果导出
│   ├── algoos_web/         # 简单前端静态资源或模板
│   └── tests/
├── protocol/
│   ├── schemas/            # 每一类 JSON 的 JSON Schema
│   ├── fixtures/           # 正常、重复、取消、乱序、过期消息
│   └── golden/             # Python/C++ 序列化一致性样例
├── problems/               # 题目源包；发布时打为 test bundle
├── migrations/             # Alembic SQL 迁移
├── deploy/
│   ├── compose/            # control VM 的 NATS/Postgres/MinIO/Redis
│   ├── systemd/            # Worker 服务、cgroup Delegation
│   ├── cloud-init/         # VM 账号、依赖、目录、时间同步
│   ├── inventory/          # 脱敏的节点角色和实验配置
│   ├── scripts/            # 发布、健康检查、清队列、快照前检查
│   └── env.example
├── experiments/
│   ├── workloads/          # JSONL 到达 trace 与原始来源说明
│   ├── profiles/           # 算法、资源、缓存、随机种子配置
│   ├── simulator/          # 离散事件模拟器
│   ├── runners/            # AlgoOS/DOMjudge 回放适配器
│   ├── analysis/           # 指标、置信区间、绘图
│   └── reports/            # 自动生成的表和图，不提交原始大输出
├── artifacts/
│   ├── system/             # 单机/集成证据
│   ├── cluster/            # 集群部署与故障证据
│   └── experiments/        # 每次实验的原始 CSV、环境清单、摘要
├── legacy/
│   └── single_node/        # 当前 judge/ 与 test_pipeline.py 的冻结基线
└── docs/
    ├── design/
    ├── ops/
    ├── project/
    ├── specs/
    └── tasks/
```

### 2.1 现有目录的迁移与删除规则

| 当前资产 | 目标位置/处理 | 删除前验收 |
|---|---|---|
| `judge/native/eval_server.cpp` | 拆入 `cpp/src/runner/`，保留 `legacy/single_node/` 冻结副本 | 新 Runner 在相同题包的 verdict 全一致 |
| `judge/pipeline.py` | 拆为 Gateway、Compile Worker、Aggregator 的职责 | API→队列→Worker→DB 集成通过 |
| `judge/problem.py` | Python 题目包校验器；发布时生成 bundle manifest | 问题包 schema 和打包测试通过 |
| `test_pipeline.py` | 迁移期只读基线，最终移至 `legacy/single_node/` | 新 `algoos_cli replay` 覆盖其功能 |
| `submissions/`、`result/` | 删除运行时语义，改用 MinIO + PostgreSQL；只保留 `.gitkeep` 前不应作为证据 | 端到端对象上传、查询和清理通过 |
| 旧 Makefile | 仅 legacy 使用；主线统一 CMake | CMake Debug/Release/ASan 三套构建通过 |

不要在迁移中复制两套业务判定逻辑。`classify(Evidence)`、题包解析规则和 `common` 比对规则必须各只有一个生产实现。

---

## 3. 代码模块、接口和三人分案

### 3.1 C++ 生产模块

| 模块 | 核心类/函数 | 输入与输出 | 负责人 |
|---|---|---|---|
| `protocol` | `CompileTask`、`EvalTask`、`JudgeResult`、`to_json/from_json` | JSON ↔ 强类型 DTO | 系统 + 算法复核 |
| `runner` | `ProcessRunner`、`MemcmpComparator`、`Evidence`、`classify()` | EvalTask → Evidence/Result | 系统负责人 |
| `sandbox` | `SandboxManager`、`RlimitSandbox`、`IsolateSandbox` | executable + limits → pid/result | 系统负责人 |
| `cache` | `LocalCache`、`BundleExtractor`、`CacheIndex` | hash → 只读本地路径 | 系统负责人 |
| `broker` | `JetStreamConsumer`、`ResultEmitter`、`IdempotencyGuard` | 消息确认、重试、去重 | 系统负责人 |
| `compile-worker` | `CompilePool`、`CompileLimiter`、`ArtifactUploader` | compile task → bin/CE event | 系统负责人 |
| `eval-worker` | `RunnerPool`、`CancelRegistry`、`WallDog` | eval task → result / special task | 系统负责人 |
| `special-worker` | `SpecialJudgeServer`、`SpecialLimiter`、`SpecialReplyRouter` | special task → targeted result/fallback | 系统负责人 |
| `scheduler` | `OnlineScheduler`、`AffinityRouter`、`PreplacementPlanner` | pending + node state → dispatch/plan | 算法负责人 |
| `simulation` | `DiscreteEventSimulator`、`MetricCollector` | trace + strategy → metrics | 算法负责人 |

所有公共 C++ 接口都写 Doxygen。高频运行路径不得通过 Python 回调；Python 与 C++ 之间只共享 JSON schema、对象 hash 和数据库字段定义。

### 3.2 Python 生产模块

| 模块 | 职责 | 负责人 |
|---|---|---|
| `algoos_api` | `/api/v1/submissions`、查询、取消、实验启动/下载；Pydantic 校验 | 应用与测试负责人 |
| `algoos_control.repositories` | PostgreSQL 事务、状态迁移、幂等 UPSERT | 系统负责人 |
| `algoos_control.object_store` | MinIO 上传、对象 hash 校验、TTL 清理 | 系统负责人 |
| `algoos_control.publisher` | Gateway/Scheduler 的 NATS 发布与 outbox | 系统负责人 |
| `algoos_control.aggregator` | 消费 eval result、提交级收敛、取消决策、写 DB | 系统负责人 |
| `algoos_control.heartbeat` | Redis 心跳、节点能力、缓存摘要 | 系统负责人 |
| `algoos_web` | 提交、结果详情、节点状态、实验比较页面 | 应用与测试负责人 |
| `algoos_cli` | 题包发布、trace 回放、故障注入、导出 CSV | 算法 + 应用协作 |

### 3.3 按提交可独立验收的工作包

| 包 | 内容 | 交付证据 |
|---|---|---|
| P0 协议冻结 | schema、枚举、golden message、版本策略 | Python/C++ 往返解析和 schema 测试 |
| P1 Runner 重构 | 旧单机服务拆为 Runner + compare + classify | AC/WA/CE/RE/TLE/MLE/OLE 的回归证据 |
| P2 控制面 | API、DB migration、MinIO source 上传、compile task | `POST` 返回 202，DB 有 QUEUED，NATS 有消息 |
| P3 编译集群 | 远程编译、日志、bin 内容寻址、CE 事件 | 成功/CE/编译超时三组测试 |
| P4 评测集群 | 多 Worker、缓存、normal compare、result event | 两节点分发及结果聚合 |
| P5 取消与有序短路 | CancelRegistry、滑动窗口、用户取消 | 乱序完成仍得到编号最小失败点 |
| P6 特判和回退 | special worker、relay、心跳、fallback | 特判 AC/WA、目标节点下线结果不丢失 |
| P7 调度与模拟 | 策略插件、trace、SA、指标导出 | 固定 seed 可重现实验 |
| P8 部署与演示 | VM 脚本、systemd、仪表页、故障剧本 | 冷启动到端到端演示录屏/日志 |

---

## 4. 全链路实现设计

### 4.1 提交与编译

1. Gateway 校验语言、源代码大小、题目存在性和白名单 compile profile；不接受任意用户传入的编译参数。
2. Gateway 对源码计算 SHA-256，上传 `source/<hex>.txt`；DB 事务写入 `submissions(QUEUED)`、测试点快照和 outbox。
3. Outbox relay 发布 `compile.task`，避免“DB 已提交但消息丢失”。
4. Compile Worker 以 `msg_id` 去重，下载源码后在独立临时目录调用固定 `g++ -std=c++23 -O2 -march=native -pipe` profile。
5. 编译成功：生成 `bin_hash = sha256(source_hash + profile_id + compiler_version)`，上传可执行文件、更新 DB、按题目快照产生 `eval.task`；失败：上传/记录截断编译日志，产生 CE 终态事件。

编译的 CPU、内存、墙钟和输出上限独立于题目运行限制。编译 Worker 只能编译 C++，不能执行编译产物。

### 4.2 评测与统一 `classify()`

Eval Worker 消费任务后的固定流程：

```text
收到 eval.task
  → schema/版本检查、judge_id 幂等检查
  → CancelRegistry 检查
  → 下载或命中 bin/tests 本地缓存
  → 创建私有 workdir/tmpfs 输出目录
  → SandboxManager.spawn()
  → WallDog + wait4 + cgroup 统计
  → Evidence
  → classify(Evidence)
  → normal: common comparator → AC/WA
  → special: 上传实际输出，投 special.task，释放运行槽
  → 投 eval.result；非 AC 时请求取消
```

`Evidence` 至少包含 `wait4` 退出状态、退出码、信号、CPU 时间、`ru_maxrss`、墙钟看门狗、cgroup `memory.events.oom_kill`、`memory.peak`、输出字节数和 limits。资源判定优先级严格按 `next_step.md`：OOM → wall timeout → SIGXCPU → 崩溃信号 → 非零退出 → 内存峰值 → CPU 超限 → 输出超限 → 比较输出。

`Verdict` 与 `Reason` 分离：例如教学模式可返回 `OLE/OLE_TRUNCATED`，竞赛兼容模式可向外映射为 `WA/OLE_TRUNCATED`，但原始 reason 必须保留。

### 4.3 沙箱分层

| 层 | 首版实现 | 验证点 |
|---|---|---|
| 进程与资源 | `fork/exec`、进程组、`RLIMIT_CPU/AS/FSIZE/CORE`、`wait4` | TLE/MLE/RE/OLE 基线 |
| cgroup v2 | `memory.max`、`memory.swap.max=0`、`pids.max`、`cpu.max`、`memory.peak` | OOM kill 与内存峰值 |
| 文件系统 | 私有 workdir、只读 bin/tests、受限 tmpfs、清理器 | 无写入宿主、输出限额 |
| namespace | PID、mount、network、UTS、IPC | 被测程序不能访问网络/宿主进程 |
| syscall | seccomp allowlist 或成熟工具封装 | 禁止危险系统调用 |

实现顺序是 `RlimitSandbox` → `IsolateSandbox`/受控 helper → `CgroupSandbox` → namespace/seccomp。不得在无 root/cgroup 委派条件的 WSL 环境声称上述功能通过；最终证据必须来自评测 VM。

### 4.4 特判、Relay 和 Fallback

1. normal 资源类错误直接产生 `eval.result`，不进入特判。
2. special 任务将 `out_hash`、`ans_hash`、checker hash、限制、`reply_to(node_id, session_id, deadline)` 投入 `special.task`。
3. Eval Worker 上传输出后立即释放 Runner slot；不能同步阻塞等待 checker。
4. Special Worker 在独立受限沙箱运行 checker，产生特判 verdict。
5. `SpecialReplyRouter` 查询 Redis 心跳：目标节点存活则发布 `special.result.<node_id>`；失活或 deadline 过期则投 `special.fallback`。
6. 原 Eval Worker 的 `SpecialResultRelay` 只接受自己的 node/session，读取本地取消状态，补 `source=special` 和 `short_circuited` 后投 `eval.result`。
7. FallbackConsumer 将无主结果转换为标准 `eval.result`，不能让结果因原 Worker 下线永久悬挂。

特判 checker 也属于不可信/可能异常的执行对象，必须有独立的时间、内存、输出和文件访问限制。

### 4.5 取消、短路和幂等

- `cancel` 分为 `USER_CANCELLED` 与 `SHORT_CIRCUIT`，两者在 DB 和展示层必须可区分。
- Worker 收到取消后，未 fork 的任务产生 `CANCELLED/SHORT_CIRCUITED` 结果；已 fork 的任务终止其进程组并仍须回传可解释终态。
- 每个 `judge_id=file_id:test_id:attempt` 只允许一个最终测试点结果。消息可能重复投递，消费者用 DB 唯一键和 `INSERT ... ON CONFLICT` 去重。
- Aggregator 不能仅以“收到的结果数”判断完成；它读取提交时保存的测试点快照、派发状态和 short-circuit 边界。
- 所有消息带 `ver`、`msg_id`、UTC 时间和可追踪的 `correlation_id=file_id`；新增字段只能向后兼容地增加。

### 4.6 状态机

```text
QUEUED → COMPILING → COMPILED → EVALUATING → FINAL
                    ↘ CE

EVALUATING → PENDING_SPECIAL → EVALUATING
EVALUATING/PENDING_SPECIAL → SHORT_CIRCUITED → FINAL
QUEUED/COMPILING/EVALUATING → CANCELLED → FINAL
```

`FINAL` 只有聚合器能写入。Worker 不允许直接覆盖 submission verdict；这能避免多个节点竞争写终态。

---

## 5. 接口与数据契约

### 5.1 HTTP API

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/v1/submissions` | 上传 C++ 源码，返回 `202/file_id` |
| `GET` | `/api/v1/submissions/{file_id}` | 提交状态、最终 verdict、逐测试点详情 |
| `POST` | `/api/v1/submissions/{file_id}/cancel` | 用户取消 |
| `GET` | `/api/v1/nodes` | 节点心跳、容量、缓存摘要（管理员） |
| `POST` | `/api/v1/experiments` | 固定 profile 启动 trace 回放（管理员） |
| `GET` | `/api/v1/experiments/{id}` | 实验元数据、指标和原始结果指针 |

提交请求只接受 `problem_id`、`language=cpp23` 和源代码。`compile_flags`、`judge_mode_override` 只能在管理员调试 API 中白名单使用，不能暴露给普通用户。

### 5.2 公共消息字段

所有消息：

```json
{
  "ver": 1,
  "msg_id": "uuid-v7-or-ulid",
  "correlation_id": "file_id",
  "emitted_at": "2026-11-08T12:00:00.000Z"
}
```

哈希在消息中统一是 `sha256:<64-lowercase-hex>`，数据库和对象路径统一保存裸 hex。单位统一：时间毫秒、内存/输出字节、Linux 展示内存单独转换为 KiB。

### 5.3 六类业务消息的必备字段

| 消息 | 必备字段 | 幂等键 |
|---|---|---|
| compile task | file_id、problem_id、source_hash、compiler_profile、limits | file_id + source_hash + profile |
| eval task | file_id、test_id、judge_id、attempt、bin_hash、test_bundle_hash、limits、judge_mode、sequence | judge_id |
| eval result | judge_id、verdict、reason、CPU/内存/输出、source、short_circuited | judge_id |
| special task | judge_id、out_hash、ans_hash、checker_hash、reply_to、deadline | judge_id |
| special result | judge_id、target_eval_node、session_id、verdict、reason | judge_id + session_id |
| cancel | file_id、scope、reason、origin、sequence_boundary | file_id + cancel generation |
| fallback | judge_id、原 reply_to、fallback_reason、结果 | judge_id |

`protocol/schemas/` 必须为以上每种 payload 提供 JSON Schema；`fixtures/` 至少覆盖缺字段、未知版本、重复 `msg_id`、重复 `judge_id`、过期 session、乱序结果和取消竞态。

### 5.4 数据库最小表

| 表 | 关键字段/约束 |
|---|---|
| `submissions` | file_id PK、problem_id、source_hash、bin_hash、status、final verdict/reason、compiler profile、时间戳 |
| `submission_cases` | file_id + sequence PK、test_id、input/answer hash、limits、judge mode、派发状态；提交时冻结 |
| `test_results` | `judge_id` UNIQUE、file_id、sequence、attempt、verdict/reason、资源数据、source、short_circuited |
| `outbox` | DB 事务内创建的待发布消息，发布后记录 ack | 
| `processed_messages` | consumer_name + msg_id UNIQUE，支持至少一次消息去重 |
| `eval_nodes` | node_id、能力、版本、last_heartbeat、load_score、缓存摘要 |
| `experiments` | profile hash、workload hash、seed、git commit、镜像版本、开始/结束时间 |
| `experiment_runs` | experiment_id、strategy、repeat、原始 CSV/日志对象指针、汇总指标 |

PostgreSQL 是最终业务状态；Redis 取消集或心跳丢失后应能由 DB/消息重建。

### 5.5 对象存储规范

```text
source/<source_sha256>.cpp
bin/<bin_sha256>.bin
tests/<problem_id>/<bundle_sha256>.tar.zst
checker/<checker_sha256>.bin
out/<file_id>/<test_id>/<attempt>/<out_sha256>.out        # 24 h TTL
logs/compile/<file_id>/<attempt>.log                       # 7 d TTL
evidence/<experiment_id>/<node_id>/<timestamp>.json        # 实验归档
```

上传后必须二次计算/验证 hash。Worker 本地缓存只把 hash 映射到只读文件，不信任消息中的任意路径。

---

## 6. 在线调度与离线预放置实现方案

### 6.1 运行期贪心堆调度

调度器输入为待运行任务、节点可用槽位、预计服务时间、缓存摘要、健康状态和当前时间。输出为不可变 `DispatchDecision(judge_id, node_id, policy, score, explanation)`，每个决策必须落入实验日志。

| 策略 | 堆键 | 目的与风险 |
|---|---|---|
| FCFS | `(submitted_at, sequence)` 最小堆 | 可解释基线，重尾任务会阻塞短任务 |
| SJF | `predicted_service_ms` 最小堆 | 降低平均等待，可能饥饿长任务 |
| Aging-SJF | `p_hat - alpha * waiting_time` 最小堆 | 以等待补偿控制饥饿 |
| Earliest-Finish | 节点 `predicted_free + p_hat` 最小堆 | 选择预计最早完成节点 |
| Cache-aware | `finish + beta * miss_bytes + gamma * cold_start` | 兼顾负载与数据局部性 |

实现细节：任务和节点使用版本号 + lazy invalidation；更新优先级时推入新条目，弹出时核对版本，避免在 `std::priority_queue` 中做不安全的原地修改。预测模型首版为“同题目/同 bundle/同语言的历史中位数 + 冷启动全局 p50”，训练 trace 与测试 trace 必须分开，报告预测 MAE/MAPE。

### 6.2 批量预放置与模拟退火

这不是实时调度，而是对已知 workload window 的**离线任务映射和缓存预热**。输入为某一批已知任务、节点容量、初始缓存和网络代价；输出为 `task → node`、`object → prewarm nodes`。

```text
J = w1 * makespan
  + w2 * load_stddev
  + w3 * transfer_bytes
  + w4 * cache_miss_count
  + w5 * overloaded_slots
```

| 对照 | 初始解/搜索 | 目的 |
|---|---|---|
| Random | 固定随机种子 | 下界质量较差的参照 |
| Greedy-LPT | 按预计时长降序，放入当前最轻节点 | 负载均衡基线 |
| Greedy-Affinity | LPT 基础上优先已有 bin/bundle 的节点 | 数据局部性基线 |
| SA | Greedy-Affinity 初始解；move/swap 邻域；固定降温表 | 检验局部搜索的额外收益 |

SA 配置必须写入 profile：初温、终温、降温率、最大迭代、move/swap 比例、权重和 seed。先用离散事件模拟跑大规模 trace，再将代表性 batch 在真实 VM 预热和回放，验证模拟预测与实测排序是否一致。

---

## 7. KVM/QEMU 部署方案

### 7.1 最小完整拓扑

主机为 24 核时，建议保留至少 4 个物理核心给宿主机，采用五台 VM。该拓扑覆盖 `next_step.md` 的全部角色，同时总 vCPU 不超过 14。

| VM | 规格建议 | 服务 | 说明 |
|---|---:|---|---|
| `algoos-control` | 2 vCPU / 6 GiB | API、PostgreSQL、Redis、NATS、MinIO、Aggregator、Fallback | 首版单点控制面 |
| `algoos-compile-01` | 2 vCPU / 4 GiB | Compile Worker | 编译与评测资源隔离 |
| `algoos-eval-01` | 4 vCPU / 6 GiB | Eval Worker、Relay、缓存 | 评测节点 1 |
| `algoos-eval-02` | 4 vCPU / 6 GiB | Eval Worker、Relay、缓存 | 评测节点 2 |
| `algoos-special-01` | 2 vCPU / 4 GiB | Special Worker | 特判隔离；无特判负载时保持空闲 |

后续扩容仅复制 `eval-*` 或 `compile-*` 镜像并登记 node_id。所有评测节点必须是相同 OS、内核、GCC、评测镜像和 cgroup 配置；不同硬件不混合计算性能结论。

### 7.2 虚拟网络、存储与安全

- libvirt 建立私有 `algoos-net`，控制节点固定 IP；Worker 仅允许访问控制节点的 NATS、MinIO、Redis 和时间同步服务。
- 管理 SSH 只从宿主机进入；外部浏览器只访问 control VM 的 API 端口。
- Worker 不拥有生产数据库账号；使用最小权限 NATS 凭证、MinIO bucket policy 和短期预签名 URL。
- 密钥、密码、IP 真实清单只放 VM `/etc/algoos/*.env`，仓库仅提交 `env.example` 与脱敏 inventory。
- Worker 的 local cache 置于 `/var/lib/algoos/cache`，workdir 置于独立 tmpfs 或受限磁盘分区；不共享宿主机目录给被测程序。
- 使用 `chrony`/NTP；日志记录 UTC，展示时再转换时区。

### 7.3 控制节点与 Worker 服务

控制 VM 用 Compose 或 systemd 管理 PostgreSQL、NATS JetStream、Redis 和 MinIO；生产服务健康检查包括 DB 事务、NATS stream、Redis ping 和 MinIO bucket。Worker 使用 systemd：

```text
After=network-online.target
Restart=on-failure
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
Delegate=yes              # 允许 SandboxManager 创建受管 cgroup
ReadWritePaths=/var/lib/algoos /run/algoos
```

每个 Worker 启动时向 `eval_nodes`/Redis 写入 `node_id`、版本、可用 slot、cache 摘要和心跳。健康检查失败后 Scheduler 停止派发，SpecialReplyRouter 开始 fallback。

### 7.4 部署顺序与验收

1. 创建并快照五台同版本 VM，记录镜像 hash、内核、GCC、CPU pinning、内存与磁盘配置。
2. control VM 启动基础服务，创建 bucket、stream、数据库迁移和最小权限账号。
3. 每个 Worker 安装同一 release，`--health`、NATS 连接、缓存目录权限和 cgroup 委派检查通过。
4. 单 Worker 执行 AC/WA/CE/RE/TLE/MLE/OLE；核验 DB、MinIO 和结果事件一致。
5. 两 Eval Worker 并发回放，确认任务跨节点分布且 verdict 与单机基线一致。
6. 启用取消、特判、节点 kill、fallback，保存可重放事件日志。
7. 创建“实验前快照”；每轮实验恢复快照、导入固定 trace、运行、归档、清理。

---

## 8. 测试、质量与可观测性

### 8.1 测试层次

| 层级 | 范围 | 必测案例 |
|---|---|---|
| C++ 单元 | classify、比较器、优先队列、SA 邻域、缓存索引 | 判定优先级、lazy invalidation、固定 seed |
| Python 单元 | schema、仓储、状态机、outbox、API 验证 | 字段错误、重复事件、非法状态迁移 |
| 契约 | Python/C++ 的 golden JSON | 同一 payload 序列化/反序列化等价 |
| 集成 | NATS+Postgres+MinIO+一个 Worker | 重投、ACK 失败、对象缺失、CE |
| 集群 | 多 VM | 同时提交、乱序回传、节点失联、特判回退 |
| 回归 | 现有题目与异常提交 | verdict 与 legacy 基线一致 |
| 性能 | 固定 trace 回放 | 指标完整、环境可复现 |

### 8.2 构建规范

- C++：CMake，`-std=c++23 -O2 -march=native -pipe`；Debug 测试加入 `-g -ggdb3 -fsanitize=address,undefined`；`ctest --output-on-failure`。
- Python：`pyproject.toml + uv.lock`，`uv sync`、`uv run pytest -v`、`uv run ruff format --check`、`uv run ruff check`。
- CI 首先执行单元/契约/格式检查；涉及 cgroup、namespace、KVM 的测试只在标签为 `vm-integration` 的真实环境运行。

### 8.3 观测字段

每个任务至少记录：排队开始/结束、编译开始/结束、运行开始/结束、目标节点、调度策略与 score、cache hit/miss、下载字节、CPU ms、wall ms、memory peak、输出字节、verdict/reason、重试次数、消息 ID、judge ID、镜像和 Git commit。

日志采用 JSON Lines，并以 `correlation_id=file_id` 串联 Gateway、Worker、Aggregator；实验输出必须包含环境清单和 hash，而非只保存屏幕截图。

---

## 9. 实验设计

### 9.1 固定变量

- 相同 KVM 快照、vCPU pinning、内存、磁盘、Linux 内核、GCC 版本、题包、时限和测试数据；
- 任务 trace 使用 JSONL，包含到达时间、题目、提交类别、预计服务时间训练集标识和随机 seed；
- 每个配置独立冷缓存和预热缓存两组；运行前清空 NATS stream、DB 实验数据和 Worker cache；
- 每种配置至少 15 次重复，报告中位数、p50/p95/p99、95% bootstrap CI，保留每次原始 CSV。

### 9.2 工作负载

| 组 | 内容 | 作用 |
|---|---|---|
| Micro | 短/中/长/重尾服务时间 | 测试队列策略的基本差异 |
| Correctness | AC/WA/CE/RE/TLE/MLE/OLE | 验证分布式结果口径 |
| Burst | 10、50、100、500 份集中到达 | 检验吞吐与尾延迟 |
| Cache | 同题重复、题目交替、大测试包 | 检验缓存亲和和预热 |
| Failure | eval/special 节点中途下线、重复投递 | 检验幂等、回退与恢复 |
| Real replay | 当前 `lazy_gold`、`sabotage` 和扩展题包 | 与真实题目联系 |

### 9.3 AlgoOS 与 DOMjudge 对比

| 原则 | 做法 |
|---|---|
| 同硬件 | 同一组 VM 顺序运行，不允许两系统并行争抢资源 |
| 同语义 | 使用 `all_cases`，相同 C++ 编译器、题包、普通比较规则、时限和内存限制 |
| 同负载 | 同一 source/arrival trace，通过各自适配器提交 |
| 冷热分离 | 明确区分第一次冷缓存和预热后稳定吞吐 |
| 数据完整 | 外部回放器记录客户端提交/终态时间，系统内部指标单独保存 |
| 不公平项 | 特判异步、短路、故障恢复、缓存预热不与未等价配置的 DOMjudge直接比较 |

输出：verdict 一致率、端到端吞吐、平均/p95/p99 周转、队列等待、资源利用率。DOMjudge 的版本、judgehost 数、并行开关、chroot/cgroup 配置必须写进实验环境清单；其 judgehost 的 Linux、网络、root 与 cgroup v2 前提应按[官方部署文档](https://www.domjudge.org/docs/manual/main/install-judgehost.html)逐项核对。

### 9.4 调度与预放置矩阵

| 实验 | 自变量 | 因变量 |
|---|---|---|
| 在线调度 | FCFS/SJF/Aging-SJF/Earliest-Finish/Cache-aware | 等待、周转、p95、公平性、饥饿次数 |
| 预测误差 | 完美 oracle（仅模拟）、历史中位数、冷启动 p50 | 指标相对退化、MAE/MAPE |
| 缓存亲和 | cache beta、工作集大小、缓存容量 | cache hit、传输量、makespan |
| 预放置 | Random/LPT/Affinity/SA | makespan、负载方差、数据移动 |
| SA 参数 | seed、初温、降温、迭代次数 | 成本下降曲线、运行时间、稳定性 |
| 故障 | 下线节点类型/时机 | 恢复时间、重复率、最终结果缺失率 |

Jain 公平性：`(sum wait)^2 / (n * sum(wait^2))`；同时报告最大等待，避免仅靠平均值掩盖饥饿。SA 的 oracle 仅用于模拟上界，不能作为在线调度策略参与真实公平对比。

---

## 10. 实施时间线、验收与风险控制

| 阶段 | 产物 | 完成判据 |
|---|---|---|
| A：冻结契约 | schema、DB 草图、状态机、目录迁移 PR | 全组确认字段和术语，禁止各写一套协议 |
| B：Runner 基线 | C++ Runner/classify + legacy 一致性 | 全部已有正常/异常样例一致 |
| C：控制面与编译 | API/DB/MinIO/NATS/Compile Worker | 可远程产生 CE 或 bin hash |
| D：多节点评测 | 两 Eval Worker、缓存、聚合 | 跨 VM 结果正确回流 |
| E：取消与特判 | ordered short circuit、Relay、Fallback | 乱序、下线、特判案例通过 |
| F：策略和模拟 | 在线堆策略、SA、trace、指标 | 固定 seed 重复可得相同结果 |
| G：正式实验 | AlgoOS/DOMjudge、故障和策略矩阵 | 原始数据、图表、环境清单齐全 |
| H：报告与演示 | 视频、部署文档、报告 | 每个结论可定位代码、配置和证据 |

### 风险与降级

| 风险 | 应对 |
|---|---|
| cgroup/namespace 权限不完整 | 保持 `RlimitSandbox` 正确性基线；隔离增强作为明确的独立验收项 |
| NATS/对象存储增加排障负担 | 先在 control VM 单节点 Compose 验证，再接多 VM Worker |
| 特判拖慢主线 | normal judge、聚合、策略实验先完成；特判使用单 checker 作为扩展 |
| SA 参数过多且结论不稳定 | 冻结参数网格、固定 seed、报告敏感性，禁止只挑最好一次 |
| DOMjudge 配置不等价 | 保留配置导出和 VM 快照；只比较 `all_cases` 等价场景 |
| 同物理宿主干扰 | CPU pinning、单系统顺序实验、快照恢复、记录宿主机背景负载 |

---

## 11. 交付清单

最终展示和报告至少包含：

1. 能从源码构建的 Python 控制面与 C++ Worker；
2. 五 VM 的脱敏部署清单、初始化脚本和健康检查；
3. 单机基线与分布式 verdict 一致性证据；
4. AC/WA/CE/RE/TLE/MLE/OLE、取消、重复投递、节点下线、特判/回退的测试记录；
5. 可公开的 workload、profile、seed、环境 hash 和原始实验 CSV；
6. AlgoOS 与 DOMjudge 的等价对比表；
7. 在线贪心堆策略和离线预放置/SA 的参数、图表及结论边界；
8. 一段从提交到结果回流、以及一段节点故障恢复的演示视频；
9. 本文档、`next_step.md`、接口 schema、迁移记录和每位成员的贡献说明。

该清单完成后，项目能完整覆盖 `next_step.md` 的提交、编译、分布式评测、沙箱、特判回传、取消广播、fallback、聚合、缓存与调度实验流程，同时清楚区分已经实测的能力、逻辑设计和后续高可用展望。
