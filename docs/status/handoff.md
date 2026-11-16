# AlgoOS 交接报告（2026-11-16，第 4 步进行中）

> 分支：`加班加点大赶工`  
> 工作区状态：**全部改动尚未提交**  
> 结论：**第 4 步只完成了一部分；特判 AC/WA/SE/超时已真实跑通，用户取消只验证到提交级 CANCELLED，测试点级与有序短路尚未取得完整证据。不能把第 4 步标为完成。**

---

## 1. 当前进度结论

| 项目 | 状态 | 说明 |
|---|---|---|
| 第 1–3 步 | ✅ 已验收 | 基础设施、迁移/题包、最小端到端（六种判定） |
| 第 4 步：特判 | ◐ 基本跑通 | checker exit 0/1/其它、checker 超时四条路径已实测通过 |
| 第 4 步：用户取消 | ◐ 部分 | API 提交级返回 `CANCELLED/USER_CANCELLED`；测试点级结果当时仍是 `DISPATCHED`，未等到终态 |
| 第 4 步：有序短路 | ❌ 未取得 E2E 证据 | 脚本在用户取消断言失败后中止，短路场景没有执行；只有 C++ 单元测试覆盖边界取消 |
| 第 5–11 步 | ❌ 未开始 | 缓存/重投/节点故障、集成测试目录、部署资产、实验、旧代码迁移、文档、最终验收 |

**一句话：代码修了多个真实阻断性问题，特判主链路已经证明能跑；但第 4 步还没收口，下一步必须先处理用户取消的测试点收敛，再跑有序短路。**

---

## 2. 本次已落地的改动

### 2.1 特判契约与题包

- 明确了题包契约：**出题者提供 checker，特判机在沙箱中运行；exit 0 = AC，exit 1 = WA，其它退出码/信号/超时 = SE。**
- 新增 [problems/special_pair/problem.json](problems/special_pair/problem.json)：
  - `judge_mode=special`、`compare_mode=special`、`checker=checker.cpp`；
  - checker 接收 `<input> <participant-output> <standard-answer>` 三个参数；
  - `output_limit_bytes=65536`，允许同一题的不同合法输出长度。
- 新增 [problems/special_pair/checker.cpp](problems/special_pair/checker.cpp) 与 3 组测试数据。
- 新增验证样例：
  - [examples/special_correct.cpp](examples/special_correct.cpp)
  - [examples/special_wrong.cpp](examples/special_wrong.cpp)
  - [examples/special_checker_error.cpp](examples/special_checker_error.cpp)（checker 返回 2）
  - [examples/special_checker_timeout.cpp](examples/special_checker_timeout.cpp)（checker 超墙钟）
- [protocol/policy_v1.json](protocol/policy_v1.json) 新增 `special.checker_limits`（cpu 5s、wall 5s、mem 512 MiB、out 1 MiB）和 checker 退出码契约。
- [python/algoos_control/problem.py](python/algoos_control/problem.py) 支持可选的 `output_limit_bytes`；[python/algoos_control/compile_flow.py](python/algoos_control/compile_flow.py) 物化测试点时使用该值。

### 2.2 修掉的阻断性 Bug

1. **特判永远返回 SE**
   - [python/algoos_nodes/eval_agent.py](python/algoos_nodes/eval_agent.py) 之前检查 `task["ans_hash"]`，而评测任务字段叫 `answer_hash`，导致 `_run_special` 直接以 “special task lacks object hashes” 返回 SE。
   - 已改为 `answer_hash`。

2. **特判结果被 JetStream 去重丢弃**
   - `special_result` 和 relay 产生的 `eval_result` 原来使用同一个 `msg_id`（`judge_id:special:result`）。二者写入同一个 `ALGOOS` stream，JetStream 按 `Nats-Msg-Id` 去重，导致后者被静默丢弃。
   - [python/algoos_nodes/messages.py](python/algoos_nodes/messages.py) 已把 eval result 的 `msg_id` 改为 `judge_id:<source>:eval`；golden 同步重新生成。

3. **评测 Agent 收不到取消与特判回传**
   - 原来使用 NATS core `subscribe`；实测没有触发 relay，特判结果停在 `PENDING_SPECIAL`。
   - [python/algoos_nodes/eval_agent.py](python/algoos_nodes/eval_agent.py) 已改为每节点独立的 JetStream durable 消费者：
     - `cancel-<node_id>` 消费 `algoos.cancel`
     - `special-result-<node_id>` 消费 `algoos.special.result.<node_id>`
   - 这也为第 5 步的重投/重启恢复提供了基础。

4. **有序短路错误地杀掉低序号在途任务**
   - [cpp/src/worker/cancel_registry.cpp](cpp/src/worker/cancel_registry.cpp) 原来只要收到 submission 级取消，就杀掉该提交的所有在途进程，包括序号小于失败边界、本应继续运行的任务。
   - 已改为只杀 `sequence > boundary`；`boundary` 只取更小值；`all=true` 的提交级取消不会被后续带边界的取消覆盖。
   - [cpp/tests/unit_tests.cpp](cpp/tests/unit_tests.cpp) 新增了 10 余条边界取消断言并已通过。

5. **Aging-SJF 在真实控制面上退化成 SJF**
   - [python/algoos_control/scheduler.py](python/algoos_control/scheduler.py) 原来用 `time.monotonic()` 生成毫秒时间，同时把 `arrival_ms` 固定为 `0.0`；`_wait_rounds()` 对 `arrival_ms <= 0` 直接返回 0，导致 aging 永不生效。
   - 已改为 `time.time()*1000` 与数据库 `submitted_at` 的绝对 epoch 毫秒。
   - [python/algoos_algorithms/policies.py](python/algoos_algorithms/policies.py) 的 cache-aware 选任务原来查 `bundle:<file_id>`（不存在的键），已改为按 `bin_hash`/`bundle_hash`/`cached_on` 匹配真实内容哈希。
   - [python/tests/test_algorithms.py](python/tests/test_algorithms.py) 增加了精确的 aging 与 cache-aware 用例。

6. **特判输出因尾换行被误判 OLE**
   - [cpp/src/worker/eval_service.cpp](cpp/src/worker/eval_service.cpp) 的 `run_raw` 原来用原始文件字节数对 `out_bytes` 判定；“0 5\n” 比标准答案 “0 5” 多一个换行就会被判 OLE。
   - 已改为先 `normalize_common`，再按归一化字节数判定；原始输出文件保持不动供 checker 读取。

### 2.3 测试基础设施

- 新增 [python/algoos_testing/cluster.py](python/algoos_testing/cluster.py)：
  - Docker 启动 PostgreSQL 17 / Redis 7.4 / NATS 2.11；
  - moto 提供 S3 兼容端点（当前环境无法拉取 MinIO 镜像）；
  - 真实启动 `algoos-compile/eval/special`、compile/eval×2/special agent、control、API；
  - 提供提交、轮询、取消、查库、NATS 直发、停进程等测试 API。
- 新增 [scripts/refactor_step4_e2e.py](scripts/refactor_step4_e2e.py)：第 4 步场景与证据生成。
- `pyproject.toml` 开发依赖增加 `moto[server]`，`uv.lock` 已更新。**注意：本次 uv 锁文件使用了清华 PyPI 镜像 URL，换网络环境前需确认可访问或重新 lock。**
- 当前环境通过 `uv pip install cmake ninja` 安装了构建工具到 `.venv/bin`；**这两个依赖尚未写入 `pyproject.toml`**，新环境需要重新安装或在文档里声明。

---

## 3. 实测结果

### 3.1 已通过

- C++ 单元测试：在加入取消边界测试后通过；**最后一次 `run_raw` 修改后尚未重跑全部 ctest，需要在交接后补跑。**
- Python 单元测试：改动前为 `39 passed, 1 skipped`；**最后一次多处修改后尚未重跑 pytest/ruff。**
- 第 4 步 E2E 已实际通过：
  - checker exit 0 → AC（三条测试点，source=special）；
  - checker exit 1 → WA（`SPECIAL_WA`）；
  - checker exit 2 → SE（`SYSTEM_ERROR`）；
  - checker 超过 5 s 墙钟 → SE。
- 证据目录：[artifacts/system/refactor_step4_ca178a857f4a](artifacts/system/refactor_step4_ca178a857f4a)。
  - **该 manifest 的总体 `status` 是 `FAILED`**，因为脚本在后续“用户取消”断言处中止；前四个特判用例已写入 `cases`，可以单独核对。
  - 更早的两次失败证据 `refactor_step4_a72406ec21fe`、`refactor_step4_bc15dd7d4123` 保留，不要当作通过证据。

### 3.2 未通过/未验证

- 用户取消：
  - `POST /cancel` 返回 `CANCELLED`，提交级 `verdict=CANCELLED`、`reason=USER_CANCELLED` 已观察；
  - 但测试点当时仍为 `DISPATCHED`、无 verdict，脚本立即断言失败；
  - **尚未确认**是“只是断言太早”还是“cancel durable 已收到但没有回传测试点终态”。
  - 下一步应先轮询 `per_test` 到 `DONE/CANCELLED`，再检查 `eval_results` 是否有两条 `CANCELLED/USER_CANCELLED`；若没有，查 `cancel-eval-01/02` 消费者和原生 `cancel` 命令。
- 有序短路：
  - C++ 单元测试已覆盖“边界外杀掉、边界内保留”；
  - 真实场景尚未执行，不能声称已验收。
- 特判 fallback：未测。
- 第 5–11 步全部未开始。

---

## 4. 关键未决问题（按优先级）

### P0：用户取消测试点收敛

- 现状：API 在 `cancel_user()` 中立即把 submission 置为 `CANCELLED` 并写 `finalized_at`；在途测试点的结果随后才回流。
- 需要决定/验证：
  1. 是否允许 `FINAL/CANCELLED` 先出现、per-test 后补齐（当前实现是这样）；
  2. 还是必须等所有在途 case 回传后再置终态。
- 无论哪种，测试脚本都应轮询 case 终态，而不是看到提交级 CANCELLED 就立即断言。

### P0：有序短路场景

- 目标：`short_circuit_probe`（6 个点，case 2 立即 WA，其余 sleep 2.5 s）。
- 预期：case 1 AC（低序号不被杀）、case 2 WA、case 3–6 `CANCELLED/SHORT_CIRCUITED`、最终 WA、`cancel_events` 边界为 2。
- 这是第 4 步最重要的验收，必须实际跑通并写入 manifest。

### P1：特判 fallback 与 Agent 重启

- `SpecialAgent` 用 Redis heartbeat 判断目标评测节点是否存活；失活时投 `fallback`。
- **残留问题：** `on_special_result` 仍要求 `session_id == self.session`。评测 Agent 重启后 session 会变，durable 消费者虽然能重放旧 special result，但会被 session 校验丢弃。第 5 步验证重启恢复前必须先解决：
  - 把 session 映射持久化到 Redis/DB；或
  - 允许节点级“旧 session”回传，并依赖 `judge_id` 幂等。

### P1：第 5 步节点故障

- 目前 eval task 是每节点 durable，但已派发到失联节点的 case 没有重派/失败机制；`submission_cases` 没有 `dispatched_at`，也没有 attempt 递增。
- 需要加 reaper：失联节点上超时的 `DISPATCHED` case 重新置 READY（`dispatch_generation+1`）或最终 SE；同时保证晚到的旧结果按幂等处理。

### P2：其他

- `special_service` 的 checker 限额已独立，但仍手工判定超时/内存；可后续统一到 `classify()`。
- C++ 侧 special/cancel/fallback 的严格字段校验仍不完整；cancel/fallback 目前由 Python 消费。
- 真实 cgroup OOM、seccomp 逐 syscall、编译超时仍未验证。
- `HANDOFF.md` 生成时工作区未提交；`git status` 应以实际输出为准。

---

## 5. 下一步执行顺序

1. **收口用户取消**
   - 修改 `scripts/refactor_step4_e2e.py`：取消后轮询 `per_test` 到终态，断言两条 `CANCELLED/USER_CANCELLED`。
   - 若超时，检查 `eval-01/02-agent.log`、`cancel-eval-01/02` 消费者、`eval_results` 表。
2. **跑有序短路**
   - 使用 `short_circuit_probe`，`evaluation_mode=ordered_short_circuit`、`strategy=fcfs`。
   - 断言 case 1 不被杀，case 3–6 为 SHORT_CIRCUITED，cancel boundary=2。
3. **补特判 fallback**
   - 最简单：构造一个目标评测节点无 heartbeat 的 special task，验证 fallback → control → eval_results。
   - 同时处理上面 P1 的 session 持久化问题。
4. **回归**
   - `cmake --build build/cpp --parallel 8 && ./build/cpp/algoos_unit_tests`
   - `uv run pytest -q`
   - `uv run ruff check python`
   - 重跑 `scripts/refactor_step4_e2e.py`，生成 `status: PASSED` 的新证据。
5. **进入第 5 步**
   - 缓存命中（heartbeat `cached_hashes`、本地 cache 目录）
   - NATS 重投与重复结果去重
   - 节点失联/重启/fallback 专项
6. **第 6–11 步**：pytest integration 目录、部署资产、实验运行器、legacy 迁移、README/文档、最终验收；最后再做“优化文件结构、删除冗余”。

---

## 6. 复现命令

```bash
# C++ 构建（当前环境 cmake/ninja 在 .venv/bin）
export PATH="$PWD/.venv/bin:$PATH"
cmake -S cpp -B build/cpp -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/cpp --parallel 8
./build/cpp/algoos_unit_tests

# Python 单测/静态检查
uv run pytest -q
uv run ruff check python

# 第 4 步 E2E（需要 Docker、g++、moto[server]）
PYTHONPATH=python uv run python scripts/refactor_step4_e2e.py --actor AI

# 本机服务镜像来源
# docker.m.daocloud.io/library/postgres:17-alpine
# docker.m.daocloud.io/library/redis:7.4-alpine
# docker.m.daocloud.io/library/nats:2.11-alpine
# S3 使用 moto，非 MinIO；不要把它当作生产对象存储验证。
```

---

## 7. 交接注意

- **不要把第 4 步写成“已完成”。** 准确表述：特判主链路（AC/WA/SE/超时）已通过；用户取消只到提交级；有序短路尚未 E2E。
- **不要引用 `refactor_step4_ca178a857f4a` 的 `status: FAILED` 当作整体通过。** 只能引用其中四个特判用例。
- 现有测试环境是 **本机 Docker + moto + 关闭 cgroup/namespace/seccomp 的 root 沙箱**，不是生产隔离验证。
- `special_pair` 的 `checker.cpp` 是测试契约样例，不是生产 checker 规范的全部；正式题包应继续按“exit 0/1/其它”实现。
- 工作区有大量未提交修改；继续前先 `git status`，保留所有已有改动。
- 旧单机 `judge/`、`test_pipeline.py`、`web/` 等仍在根目录；第 9 步再迁移到 `legacy/single_node/`，当前不要提前删除。
