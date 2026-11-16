# AlgoOS 分布式架构说明

> 对应实施总案与 `next_step.md` 的组件/UML。本文描述**当前代码实际实现**，
> 并明确哪些能力已在本机验证、哪些仍只在 VM/云环境成立。

## 1. 角色与进程

| 角色 | 进程 | 职责 | 入口 |
|---|---|---|---|
| API 网关 | `algoos-api` | 提交、查询、取消、节点、题目、实验、静态页面 | `python/algoos_api/app.py` |
| 控制面 | `algoos-control` | outbox 发布、编译结果入库、调度、聚合、fallback、心跳/失联扫描 | `python/algoos_control/control_service.py` |
| 编译 Worker 池 | `algoos-compile` + `compile-agent`，1..N 节点 | 固定 profile 编译、上传 bin、发布编译结果；共享 durable `compile-workers` | `cpp/apps/compile_main.cpp`、`python/algoos_nodes/compile_agent.py` |
| 评测 Worker 池 | `algoos-eval` + `eval-agent`，1..N 节点 | 缓存下载、沙箱执行、判定、短路取消、特判上传、Relay；按心跳动态调度 | `cpp/apps/eval_main.cpp`、`python/algoos_nodes/eval_agent.py` |
| 特判 Worker 池 | `algoos-special` + `special-agent`，1..N 节点 | 下载 `.out`/answer/checker，运行出题者 checker，按存活回传或 fallback；共享 durable `special-workers` | `cpp/apps/special_main.cpp`、`python/algoos_nodes/special_agent.py` |
| 基础设施 | PostgreSQL / Redis / NATS JetStream / MinIO（本机已用 Chainguard 构建的 AGPLv3 镜像验证；moto 仅回退） | 元数据、心跳与取消快路径、消息、对象 | `deploy/compose/` |

C++ 侧只负责**执行、判定、沙箱**；NATS/MinIO/Redis 的 I/O 与队列生命周期由 Python
Agent 承担。这是当前实现的明确边界：高频执行路径在 C++，队列编排在 Python。总案中
“C++ 直接消费 NATS”未作为本阶段的验收项，不能在报告中写成已完成。

### 1.1 Worker 池规模

`compile`、`eval`、`special` 三种角色都是**可水平扩展的池**，不是固定 1/2 台：

- 编译：所有 compile-agent 共享 durable `compile-workers`，同一 `compile_task` 只被一个节点消费；
- 评测：节点 ID 为 `eval-NN`；调度器从 `eval_nodes` 心跳表读取全部存活节点，按 `eval.task.<node>` 派发；
- 特判：所有 special-agent 共享 durable `special-workers`，结果按 `reply_to.eval_node_id` 回到发起评测机；
- 本机脚本默认 2/2/2，`ALGOOS_COMPILE_NODES`、`ALGOOS_EVAL_NODES`、`ALGOOS_SPECIAL_NODES` 可改；
- KVM/inventory 中的节点数量只是参考拓扑，不是协议或调度上限。

## 2. 消息与主题

| 消息 | subject | 生产者 → 消费者 |
|---|---|---|
| `compile_task` | `algoos.compile.task` | 控制面 → 编译 Worker 池（共享队列） |
| `eval_result`（compile 阶段） | `algoos.compile.result` | 编译 Worker → 控制面 |
| `eval_task` | `algoos.eval.task.<node>` | 调度器 → 指定评测 Worker |
| `eval_result`（eval 阶段） | `algoos.eval.result` | 评测 Worker/Relay/fallback → 聚合器 |
| `special_task` | `algoos.special.task` | 评测 Worker → 特判 Worker 池（共享队列） |
| `special_result` | `algoos.special.result.<eval_node>` | 特判 Worker → 发起评测机的 Relay |
| `cancel` | `algoos.cancel` | 聚合器/API → 全部评测 Agent |
| `fallback` | `algoos.fallback` | 特判 Router → 控制面 |
| heartbeat | `algoos.heartbeat` | 所有 Agent → 控制面 |

消息是**纯 UTF-8 JSON**（NATS 不加 4 字节前缀）；旧 TCP 长度前缀实现已从仓库删除，
需要时从 Git 历史取回。所有消息带 `ver=1`、`msg_id`、`correlation_id`、`emitted_at`；
未知字段保留，未知版本/类型/模式拒绝。策略见 `protocol/policy_v1.json`。

## 3. 提交状态机

```
QUEUED → COMPILING → COMPILED/CE
COMPILED → EVALUATING → (PENDING_SPECIAL) → FINAL
EVALUATING → SHORT_CIRCUITED → FINAL
QUEUED/COMPILING/EVALUATING → CANCELLED
```

- 提交与 compile task 在同一事务写入 `submissions + outbox`；outbox 至少一次发布。
- 编译失败只写 CE/SE，不产生测试点。
- 聚合器按 `judge_id` 幂等写入 `eval_results`；只有聚合器写最终状态。
- 有序短路：失败序号 s 且 1..s-1 全 AC 时发布 `cancel`，边界 s；序号 > s 的 READY
  测试点直接 CANCELLED，在途测试点由 Agent/原生 Registry 杀掉并回传 CANCELLED；
  最终判定仍是首个非 AC（不会被 CANCELLED 覆盖）。
- 用户取消：API 立即置提交级 CANCELLED；所有测试点随后收敛到
  `CANCELLED/USER_CANCELLED`。测试脚本必须等待 `per_test` 终态，不能只看提交级状态。

## 4. 沙箱与判定

`cpp/src/sandbox/sandbox.cpp` 实现：

| 层 | 实现 | 本机验证 |
|---|---|---|
| cgroup v2 | `memory.max`、`memory.swap.max=0`、`pids.max`、`cpu.max`、`memory.peak`、`memory.events` | 本机 root 单测通过；真实 OOM 未构造 |
| namespace | mount/pid/net/uts/ipc | 本机 root 单测通过（PID 1 助手 + 真实程序 PID 2） |
| seccomp | libseccomp **黑名单**（socket/mount/ptrace/clone 新命名空间等） | 已加载；未逐 syscall 验证 |
| rlimit | CORE/CPU/AS/FSIZE | 已通过 |
| 文件系统 | 只读根 + 私有 tmpfs 工作目录 + 独立进程组 | 本机通过；未做 overlay/chroot 级隔离 |
| 看门狗 | 墙钟轮询 + 进程组 kill + 取消回调 | 已通过 |

`cpp/src/sandbox/classify.cpp` 是唯一判定表：

```
SE → OOM_KILL → WALL_TIMEOUT → CPU_TIMEOUT → OUTPUT_SIGNAL
→ CRASH_SIGNAL → NONZERO_EXIT → MEM_PEAK_OVER → CPU_TIME_OVER
→ OUTPUT_SIZE_OVER → COMPARE
```

`WR` 固定以 `WR/OUTPUT_LIMIT` 输出，不再提供独立/合并模式。单独 SIGKILL
不当作 CPU 超限；MLE 必须有 cgroup 峰值或可比内存证据。

## 5. 调度与实验

- 在线策略：FCFS、SJF、Aging-SJF、Earliest-Finish、Cache-aware。
- `cache_aware` 按 heartbeat 的 `cached_hashes` 匹配真实 bin/bundle 内容哈希。
- 有序短路窗口默认 4，由 `submissions.short_circuit_window` 冻结。
- 离线：`algoos_algorithms.simulator` 离散事件模拟；`placement` 模拟退火副本放置。
- 真实集群实验走 `experiments.runner`，`data_source=cluster`；模拟走
  `experiments.simulator`，`data_source=simulation`，两者不得混写。

## 6. 可靠性

- outbox：先持久化再发布；`msg_id` 稳定，JetStream 去重。
- 结果幂等：`eval_results.judge_id` 主键 + `processed_messages`；同 judge_id 不同
  msg_id 的重复结果忽略，冲突结果保留首个并记录日志。
- Agent 重启：每节点 durable `eval-<node>`、`cancel-<node>`、`special-result-<node>`；
  ack_wait 可配置。评测 Agent 重启后旧 session 的 special result 仍被接受，
  以 judge_id 幂等。
- 节点失联：reaper 将超时的 DISPATCHED case 重置为 READY，调度到存活节点；
  旧节点迟到结果按 judge_id 去重。
- 特判 fallback：目标评测节点无 heartbeat 或回传失败时，特判结果转
  `fallback-topic`，控制面转为 eval result。

## 7. 当前边界

- 本机验收使用 Docker + 真实 MinIO 容器（Chainguard 构建的 AGPLv3 社区版，
  `RELEASE.2026-11-12T01-15-23Z`）+ 关闭 cgroup/namespace/seccomp 的 root 沙箱；
  生产 VM 仍需重新验证完整沙箱、MinIO 集群/持久化/TLS、跨机网络与时钟。
- 上游 `minio/minio` 镜像在本环境被 registry 拒绝，`dl.min.io` 社区二进制返回 410，
  `aistor` 二进制无 license 会拒绝所有 S3 操作；因此本机固定使用下述 digest：
  `cgr.dev/chainguard/minio@sha256:9158c67a...`。moto 只作为无法拉取该镜像时的回退。
- 多节点 VM 部署资产已提供，未在本机执行。
- 实验默认 1 次；正式对照至少 3 次，报告方差/分位数。
- 旧单机链路已从仓库删除（Git 历史可查），新构建/测试/文档不再依赖。
