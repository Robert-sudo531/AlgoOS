# 原生评测服务端（reroll）

按 [CoreUML.md](../../docs/design/CoreUML.md) 实现的 C++ 常驻评测服务端。它只负责**单个测试点执行**；编译、提交目录、`result/result.tsv` 和提交级聚合由根目录的 [test_pipeline.py](../../test_pipeline.py) 负责。实现原理与 UML 见 [docs/design/eval_server.md](../../docs/design/eval_server.md)。

完整部署与使用见 [deployment.md](../../docs/ops/deployment.md)。

## 组件对应

| CoreUML 组件 | 实现位置 |
| --- | --- |
| `EvalServer` | `EvalServer::run()`，监听 `127.0.0.1` |
| `RequestQueue` | `BoundedQueue<Job>`，有界队列 + 背压 |
| `ThreadPool` / `JudgeWorker` | `EvalServer` 内部 N 个线程 |
| `JudgeCore` | `run_process()`，`fork` / `execv` / `wait4` |
| `ResourceLimiter` | 子进程内 `RLIMIT_CPU` / `RLIMIT_AS` / `RLIMIT_FSIZE` / `RLIMIT_CORE` |
| `Comparator` | `trim_common()`，common 模式使用 |
| `Verdict` | `enum class Verdict`（AC/WA/TLE/MLE/RE/CE/SE） |

## 构建

依赖：`g++`、`make`、`json-c`。

```bash
make -C judge/native
# 产物：judge/native/eval_server

make -C judge/native sanitize
# 产物：judge/native/eval_server_asan
```

## 运行

通常由 `test_pipeline.py` 自动启动，不需要手动运行。手动启动：

```bash
judge/native/eval_server --port 9000 --workers 4 --queue 64
```

可选参数：`--port`、`--workers`、`--queue`。

## 协议

4 字节大端长度 + JSON，一次连接一次请求/一次响应。

### 请求

```json
{
  "ver": 1,
  "file_id": "001__lazy_gold",
  "problem_id": "lazy_gold",
  "test_id": "1",
  "bin_path": "/abs/path/submissions/001__lazy_gold",
  "input_path": "/abs/path/problems/lazy_gold/tests/1.in",
  "answer_path": "/abs/path/problems/lazy_gold/tests/1.out",
  "result_dir": "/abs/path/submissions/001__lazy_gold__result",
  "limits": {
    "cpu_ms": 1000,
    "mem_bytes": 268435456,
    "out_bytes": 1,
    "wall_ms": 1000
  },
  "compare_mode": "common"
}
```

- `result_dir` 必须提供；C++ 把实际输出写到 `result_dir/<test_id>.out`，错误写 `<test_id>.err`。
- `compare_mode` 只接受 `common` / `special`。
- `wall_ms` 可省略或传 0，此时等于 `cpu_ms`。
- `out_bytes` 是 common 的语义输出上限，由题目标准答案 trim 后大小派生；允许 0。

### 响应

```json
{
  "ver": 1,
  "file_id": "001__lazy_gold",
  "problem_id": "lazy_gold",
  "test_id": "1",
  "verdict": "AC",
  "cpu_ms": 1,
  "mem_kb": 4000,
  "out_bytes": 1,
  "exit_code": 0,
  "signal": 0,
  "wall_timeout": false,
  "stderr_tail": "",
  "error": ""
}
```

- `cpu_ms` 来自 `wait4` 的 `ru_utime + ru_stime`。
- `mem_kb` 来自 `ru_maxrss`（Linux KB）。
- `out_bytes` 是 trim 后实际输出字节数。
- `error` 只在 `SE` 时填写。

## 比较与判定

### common

1. CRLF/CR → LF。
2. 去掉每行末尾空格/制表符。
3. 去掉文件末尾空行。
4. 与标准答案严格相等 → AC，否则 WA。

### special

本期只保留枚举。`compare_mode=special` 时 C++ 直接返回：

```text
verdict = SE
error = "compare_mode=special 尚未实现：本期不提供 checker"
```

不读答案、不 fork。

### 输出上限

- 语义上限：`out_bytes` = 标准答案 trim 后字节数；trim 后实际输出超过 `out_bytes` → WA。
- 磁盘安全上限：`fsize_cap = min(256 MiB, out_bytes + 4 KiB)`；原始输出文件超过 cap 或 `SIGXFSZ` → WA。
- 这样正确的末尾换行/行尾空格不会被 `RLIMIT_FSIZE` 误杀；`OLE` 不再单独存在，统一归入 WA。

### 判定顺序

```text
SE
 → 看门狗超时 / SIGXCPU / CPU 硬限制 SIGKILL（TLE）
 → SIGXFSZ（WA） / 其他信号（RE）
 → 非零退出（RE）
 → CPU 超限（TLE）
 → 内存超限（MLE）
 → 原始输出超安全 cap（WA）
 → trim 后超 out_bytes（WA）
 → 严格相等（AC / WA）
```

## 资源限制

| 请求字段 | 子进程限制 |
| --- | --- |
| `cpu_ms` | `RLIMIT_CPU`，向上取整到秒，软限制 N、硬限制 N+1 |
| `mem_bytes` | `RLIMIT_AS` |
| `out_bytes` | 语义上限；实际 `RLIMIT_FSIZE` 使用 `out_bytes + 4 KiB` 安全 cap |
| `wall_ms` | 父进程看门狗；0/缺省 = `cpu_ms` |

消息上限 1 MiB；答案读取上限 64 MiB；stderr 尾部最多 4 KiB。

## 与 Python 的分工

- `test_pipeline.py` / `judge/pipeline.py`：加载 `problems/`、编译 `submissions/<stem>.cpp`、构造请求、聚合、写 `result/result.tsv`。
- C++ 服务端：单测试点执行、资源限制、common 比对、单点 verdict。
- 提交级最终结果是 Python 的 ACM 聚合：全部 AC 才 AC，否则按测试点顺序取第一个非 AC。
- 生产版状态保存、页面接口等仍由系统负责人维护。

## 已知限制

- 没有 cgroup / Isolate / namespace 隔离，资源限制是 `rlimit` 级别。
- special checker 本期未实现，返回 SE。
- 没有认证/TLS；只监听 `127.0.0.1`。
- accept 循环同步读取请求，慢客户端会暂时阻塞新连接。
- 没有请求持久化、断线重试、跨重启队列。
