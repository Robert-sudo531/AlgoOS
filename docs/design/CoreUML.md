1. 部署图
展示进程与通信边界：编译是多进程，评测服务端是单进程+线程池，两者通过本地 Socket 通信。

flowchart LR
    subgraph Host["评测服务器主机"]
        direction TB
        PY["Python 主控进程<br/>编译调度 + 评测客户端 + 结果聚合"]
        GCC["gcc 编译进程池<br/>N 个独立进程并行"]
        SRV["C++ 评测服务端<br/>单常驻进程 · 内部线程池"]
        FS[("本地文件系统<br/>workdir/{submitID}/<br/>{testID}.bin / in / ans / out")]
    end

    PY -->|"subprocess.Popen / run<br/>多进程并行"| GCC
    PY -->|"TCP 127.0.0.1:9000<br/>长度前缀 + JSON"| SRV
    PY --- FS
    GCC ---|"写可执行文件"| FS
    SRV ---|"读 bin / in / ans，写 out"| FS
2. 组件图
展示 Python 侧与 C++ 侧的模块划分与依赖。

flowchart TB
    subgraph PY["Python 主控"]
        SM["SubmissionManager<br/>提交编排"]
        CS["CompileScheduler<br/>编译调度器"]
        EC["EvalClient<br/>Socket 客户端"]
        RA["ResultAggregator<br/>结果聚合"]
        SM --> CS
        CS --> EC
        EC --> RA
    end

    subgraph COMP["编译进程池"]
        G1["gcc #1"]
        G2["gcc #2"]
        GN["gcc #N"]
    end

    subgraph CPP["C++ 评测服务端"]
        SRV["EvalServer<br/>listen/accept"]
        Q["RequestQueue<br/>有界队列 · 背压"]
        TP["ThreadPool<br/>N workers"]
        W1["JudgeWorker #1"]
        W2["JudgeWorker #2"]
        WN["JudgeWorker #N"]
        JC["JudgeCore<br/>fork/exec/wait4"]
        CMP["Comparator<br/>输出比对"]
        SRV --> Q
        Q --> TP
        TP --> W1
        TP --> W2
        TP --> WN
        W1 --> JC
        W2 --> JC
        WN --> JC
        W1 --> CMP
        W2 --> CMP
        WN --> CMP
    end

    CS -->|"subprocess"| G1
    CS -->|"subprocess"| G2
    CS -->|"subprocess"| GN
    EC <-->|"Socket"| SRV
3. 时序图（Sequence）
一次提交从编译到评测返回的完整交互。编译阶段用 par 表示并行 gcc。

sequenceDiagram
    autonumber
    actor U as 用户/上游
    participant SM as SubmissionManager
    participant CS as CompileScheduler
    participant G as gcc 进程(多个)
    participant EC as EvalClient
    participant SRV as EvalServer(C++)
    participant Q as RequestQueue
    participant W as JudgeWorker
    participant P as 被测程序
    participant CMP as Comparator

    U->>SM: submit(fileID, problemID, source)
    SM->>CS: compile_all(submission)
    par testID=1
        CS->>G: subprocess gcc -o {submitID}/1.bin src
        G-->>CS: returncode + stderr
    and testID=2
        CS->>G: subprocess gcc -o {submitID}/2.bin src
        G-->>CS: returncode + stderr
    and testID=N
        CS->>G: subprocess gcc -o {submitID}/N.bin src
        G-->>CS: returncode + stderr
    end

    alt 任一编译失败
        CS-->>SM: CompileResult(ok=false, log)
        SM-->>U: CE
    else 编译全部成功
        loop 每个 testID
            CS->>EC: build EvalRequest{fileID, problemID, testID, binPath, inPath, ansPath, limits}
        end
        par 并发评测
            EC->>SRV: connect + send(len + JSON)
            SRV->>Q: enqueue(req)
            Q->>W: dequeue
            W->>W: 打开 in/ans，apply ResourceLimiter
            W->>P: fork + exec(binPath)，stdin/out/err 重定向
            P-->>W: wait4 → status + rusage
            W->>CMP: compare(stdout, answer, mode)
            CMP-->>W: AC/WA
            W-->>SRV: JudgeResult{fileID,testID,verdict,cpu,mem,stderr_tail}
            SRV-->>EC: send(len + JSON)
        end
        EC->>RA: collect(results)
        RA->>RA: 按 fileID 聚合（全 AC 才 AC，否则首个非 AC）
        RA-->>SM: FinalVerdict
        SM-->>U: {fileID, problemID, verdict, per_test[]}
    end
4. 类图
核心类与枚举，包含关键字段与方法。

classDiagram
    class SubmissionManager {
        +submit(file_id, problem_id, source) Submission
        +run() FinalVerdict
    }
    class CompileScheduler {
        -ThreadPoolExecutor pool
        -int cpu_limit
        -int mem_limit
        +compile_all(tasks) Dict~testID,CompileResult~
        +compile_one(src, out, limits) CompileResult
    }
    class EvalClient {
        -str host
        -int port
        +evaluate(reqs, workers) List~JudgeResult~
        -send_msg(sock, bytes)
        -recv_msg(sock) bytes
    }
    class ResultAggregator {
        +aggregate(results) Dict~fileID,Verdict~
    }
    class EvalRequest {
        +str file_id
        +str problem_id
        +str test_id
        +str bin_path
        +str input_path
        +str answer_path
        +int cpu_limit_ms
        +int mem_limit_bytes
        +int out_limit_bytes
        +int wall_limit_ms
        +str compare_mode
        +to_json() bytes
    }
    class JudgeResult {
        +str file_id
        +str test_id
        +Verdict verdict
        +int cpu_ms
        +int mem_kb
        +int exit_code
        +int signal
        +str stderr_tail
        +to_json() bytes
    }
    class EvalServer {
        -int listen_fd
        -ThreadPool pool
        -RequestQueue queue
        +start()
        +accept_loop()
        -handle_client(fd)
    }
    class JudgeWorker {
        +process(req) JudgeResult
        -judge_core(req) RunStat
    }
    class JudgeCore {
        +fork_exec_wait(bin, in, out, err, limiter) RunStat
        -apply_rlimits(limiter)
    }
    class ResourceLimiter {
        +int cpu_sec
        +long mem_bytes
        +long fsize_bytes
        +int wall_ms
        +apply() void
    }
    class Comparator {
        +compare(out_bytes, ans_bytes, mode) Verdict
    }
    class Verdict {
        <<enumeration>>
        AC
        WA
        TLE
        MLE
        RE
        OLE
        CE
        SE
    }

    SubmissionManager --> CompileScheduler
    SubmissionManager --> EvalClient
    SubmissionManager --> ResultAggregator
    CompileScheduler --> EvalRequest : 构造
    EvalClient --> EvalRequest : 序列化
    EvalClient --> JudgeResult : 反序列化
    EvalRequest --> ResourceLimiter : 提供参数
    EvalServer --> JudgeWorker
    EvalServer --> RequestQueue
    JudgeWorker --> JudgeCore
    JudgeWorker --> Comparator
    JudgeCore --> ResourceLimiter
    JudgeResult --> Verdict
    Comparator --> Verdict
5. 活动图：JudgeWorker 判定流程
这是 C++ worker 的核心逻辑，包含所有判定优先级与超时/信号处理。

flowchart TD
    Start([Worker 取到请求]) --> Load["打开 input / answer 文件"]
    Load --> LoadOK{"打开成功?"}
    LoadOK -->|否| SE["返回 SE"]
    LoadOK -->|是| Init["初始化临时目录<br/>创建 out/err 文件"]
    Init --> Fork["fork()"]

    Fork --> Child["子进程:<br/>ResourceLimiter.apply()<br/>dup2(in→0, out→1, err→2)<br/>execl(bin_path)"]
    Child --> ExecOK{"exec 成功?"}
    ExecOK -->|否| C127["_exit(127)"]
    ExecOK -->|是| Run["被测程序运行"]

    Fork --> Parent["父进程: 启动 wall 看门狗<br/>poll + 计时"]
    Parent --> Watch{"墙钟超时?"}
    Watch -->|是| Kill["kill(pid, SIGKILL)"]
    Watch -->|否| Wait["wait4(pid, &status, &rusage)"]
    C127 --> Wait
    Kill --> Wait

    Wait --> SigChk{"WIFSIGNALED?"}
    SigChk -->|"SIGXCPU"| TLE1["TLE"]
    SigChk -->|"SIGKILL 且来自看门狗"| TLE2["TLE"]
    SigChk -->|"SIGSEGV / SIGABRT / SIGFPE 等"| RE1["RE"]
    SigChk -->|否| ExitChk{"WIFEXITED 且 exit==0?"}
    ExitChk -->|否| RE2["RE"]
    ExitChk -->|是| ResChk{"资源用量检查"}

    ResChk -->|"cpu_time > limit"| TLE3["TLE"]
    ResChk -->|"ru_maxrss > mem_limit"| MLE["MLE"]
    ResChk -->|"out_size > out_limit"| OLE["OLE"]
    ResChk -->|"全部正常"| Cmp["Comparator.compare(out, ans, mode)"]
    Cmp --> CmpOK{"匹配?"}
    CmpOK -->|是| AC["AC"]
    CmpOK -->|否| WA["WA"]

    TLE1 --> Pack
    TLE2 --> Pack
    TLE3 --> Pack
    RE1 --> Pack
    RE2 --> Pack
    MLE --> Pack
    OLE --> Pack
    AC --> Pack
    WA --> Pack
    SE --> Pack
    Pack["打包 JudgeResult<br/>fileID / testID / verdict<br/>cpu_ms / mem_kb / signal<br/>stderr_tail"] --> Ret([返回 EvalServer])
判定优先级：SE → 被信号杀死(TLE/RE) → 非零退出(RE) → CPU超(TLE) → 内存超(MLE) → 输出超(OLE) → 比对(AC/WA)。先排除异常再谈答案比对。

6. 状态机图：提交级 Verdict
一次提交在聚合层的状态流转。

stateDiagram-v2
    [*] --> Pending
    Pending --> Compiling: 提交
    Compiling --> CE: 任一 gcc 失败
    Compiling --> Judging: 全部 gcc 成功
    Judging --> AC: 所有 test AC
    Judging --> WA: 存在 WA
    Judging --> TLE: 存在 TLE 且无更高优先级
    Judging --> MLE: 存在 MLE
    Judging --> RE: 存在 RE
    Judging --> OLE: 存在 OLE
    Judging --> SE: 存在 SE
    AC --> [*]
    WA --> [*]
    TLE --> [*]
    MLE --> [*]
    RE --> [*]
    OLE --> [*]
    SE --> [*]
    CE --> [*]
聚合规则可按需调整：常见是 首个非 AC 即最终 verdict，或按严重度排序取最高。

7. 通信协议定义
Socket 上使用 4 字节大端长度前缀 + JSON 载荷，一次请求/一次响应。

text
Request  = [uint32_be len][JSON]
Response = [uint32_be len][JSON]
请求 JSON：

json
{
  "ver": 1,
  "file_id": "sub_20261103_001",
  "problem_id": "P1001",
  "test_id": "3",
  "bin_path": "/tmp/judge/sub_20261103_001/3.bin",
  "input_path": "/data/P1001/3.in",
  "answer_path": "/data/P1001/3.ans",
  "limits": {
    "cpu_ms": 1000,
    "mem_bytes": 268435456,
    "out_bytes": 67108864,
    "wall_ms": 3000
  },
  "compare_mode": "trim_eol"
}
响应 JSON：

json
{
  "ver": 1,
  "file_id": "sub_20261103_001",
  "problem_id": "P1001",
  "test_id": "3",
  "verdict": "WA",
  "cpu_ms": 120,
  "mem_kb": 15420,
  "exit_code": 0,
  "signal": 0,
  "stderr_tail": "Assertion failed ...",
  "diff_hint": "line 5"
}