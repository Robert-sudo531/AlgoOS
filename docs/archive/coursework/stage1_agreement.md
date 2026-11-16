# AlgoOS 第一二阶段共同约定

状态：系统负责人已于 2026-10-10 阅读并认可，可作为当前实施依据；算法负责人按 A2、应用负责人按 P0/P1 继续复核，S5/A5 的共同验收尚待完成。这里的 JSON 是格式样例，尚非真实运行结果。约定变更由三人同步维护这一份文档；算法集训期间先记录待确认项，不视为三人已确认。

> **reroll（2026-11-03，待三人复核）**：当前实现已改为 `test_pipeline.py` 前台守护进程 + `submissions/` + `result/result.tsv`；旧 A+B/score/exact 段落保留为 Stage1 历史，不再作为新评测链路的实现依据。以本节和 [deployment.md](../ops/deployment.md) 为准。

## reroll 当前口径（2026-11-03，待三人复核）

### 题目配置

`problems/<problem_id>/problem.json`：

```json
{
  "problem_id": "lazy_gold",
  "title": "Lazy Gold",
  "time_limit_ms": 1000,
  "memory_limit_mib": 256,
  "compare_mode": "common",
  "tests": [
    {"case_id": "1", "input": "tests/1.in", "output": "tests/1.out"}
  ]
}
```

- `compare_mode` 只有 `common` / `special`；`special` 本期只保留枚举，C++ 返回 `SE`。
- 不使用 `score`；ACM 赛制，全部测试点 AC 才 AC，否则按顺序取第一个非 AC。
- `time_limit_ms` 同时作为 CPU 时限和 wall 时限。
- `memory_limit_mib` 转字节后作为 `RLIMIT_AS`。
- `input`/`output` 相对 `problem.json` 所在目录。
- 每个测试点的 `out_bytes` 由标准答案 `trim_common` 后的字节数自动派生，不写入 JSON。
- 忽略 legacy `score`、`output_limit_kib`、`wall_time_limit_ms`。

### common 比较

CRLF/CR → LF；去掉每行末尾空格/制表符；去掉文件末尾空行；然后严格相等。trim 后实际输出超过 `out_bytes` 归入 `WA`（OLE 不再单独存在）。

### 提交与结果

```text
submissions/
├── 00000001__lazy_gold.cpp
├── 00000001__lazy_gold
└── 00000001__lazy_gold__result/
    ├── 1.out
    └── 1.err

result/result.tsv
```

- 文件名：`<submission_id>__<problem_id>.cpp`；二进制同名去 `.cpp`；结果目录 `<stem>__result/`。
- 一份提交编译一次，所有测试点共用二进制。
- `result.tsv` 每个提交一行，列为：timestamp、source_file、problem_id、submission_id、verdict、passed、total、cases、elapsed_ms、message。
- 标准答案只读 `problems/<problem_id>/tests/<case_id>.out`；实际输出写结果目录。
- 最终判定为 ACM：全部 AC 才 AC，否则第一个非 AC。

## 以下为 Stage1 历史约定（a_plus_b/score/exact）

## 范围与文件位置

单机、C++ 提交、Python 模块、FCFS 起步。一份提交编译一次，按配置顺序运行各测试点。角色与文件目录见 [开工导航](../../README.md)。

- 真实任务的 `source_path` 相对于项目根目录。
- `problem_id` 为 `a_plus_b` 时，读取 `problems/a_plus_b/problem.json`；本期先用这一简单映射。
- 题目配置中的 `input`、`output` 相对于 `problem.json` 所在目录。
- 第一阶段源码为 `examples/a_plus_b.cpp`，测试文件为 `tests/01.in`、`01.out`、`02.in`、`02.out`。第二阶段再增加 03 和题目 JSON。
- 第二阶段每次实际演示使用独立 `run_id`；临时文件在 `work/<run_id>/<task_id>/`，结果在 `artifacts/system/<run_id>/` 或 `artifacts/batch/<run_id>/`。重复运行不能覆盖旧结果，`run_id` 可由运行入口生成，单提交调用由执行模块在内部生成。

## 三人维护边界

- 系统：维护 judge/、后端接口、状态和结果保存；先完成正确 A+B、01/02 测试，RE 样例随 S2.4 提供。
- 应用与测试：接收基础文件后维护题目包、03 测试、WA/CE 样例（P2，原 A2.2）、检查表与页面。修改共享样例先同步系统负责人；只展示真实返回的数据，不在页面重算判定。
- 算法：维护 scheduler/、模拟输入、指标公式和实验结论；主写 demo_batch.py，系统配合，应用复现并记录。
- 应用结果详情页可先用明确标注的示例 JSON（放 web/examples/，按需创建）；真实评测放 artifacts/system/ 或 batch/，模拟实验放 simulation/，三者不混作验收证据。
- 现有 pick_next/run_task 字段不变。未来 Web 接口由系统与应用共同确认，实验展示字段由算法与应用确认，再更新本文件，不预先要求实现整套 API。

## 一份真实任务

```json
{"task_id":"T1","problem_id":"a_plus_b","source_path":"examples/a_plus_b.cpp","enqueue_seq":1}
```

`task_id` 在一批任务中唯一；`enqueue_seq` 是入口按提交先后分配的唯一递增序号。FCFS 只按序号选择，不读取源码或预知运行耗时。第一阶段模拟选择顺序只需要任务编号和序号。

## 两个模块怎样配合

| 接口 | 输入 | 输出 | 约定 |
| --- | --- | --- | --- |
| `pick_next(queued_tasks)` | 待执行任务列表 | 任务编号；空列表返回 `None` | 只选择，不修改列表、不运行程序 |
| `run_task(task)` | 上述真实任务 | 下文的结果字典 | 内部定位题目、编译、运行、比对与汇总 |

批量入口维护待执行列表：选编号 → 查到任务 → 调用执行模块 → 保存结果 → 移除已处理任务。当前一份提交失败时仍继续处理下一份；环境级错误应明确报告原因。两个接口先用普通 Python 函数实现。

## 第二阶段题目配置样例

```json
{
  "problem_id":"a_plus_b",
  "title":"A+B",
  "time_limit_ms":1000,
  "memory_limit_mib":256,
  "tests":[
    {"case_id":"01","input":"tests/01.in","output":"tests/01.out","score":30},
    {"case_id":"02","input":"tests/02.in","output":"tests/02.out","score":30},
    {"case_id":"03","input":"tests/03.in","output":"tests/03.out","score":40}
  ]
}
```

三组输入依次为 `1 2`、`-2 5`、`0 0`，预期输出依次为 `3`、`3`、`0`，均以换行结束。题目总分由测试点分值相加；第一阶段只需前两组文件，不提前创建引用第三组文件的配置。

`score` 当前采用非负整数，允许 0；字符串、布尔值和小数不作为合法分值。总分按各测试点分值相加，不普遍要求等于 100：正式 A+B 仍按三组 30/30/40 验收，开发用的临时两组配置为 30/30、总分 60。此分值类型细则于 2026-10-25 随系统实现补充，算法与应用负责人的复核仍待取得，不视为三人共同确认。

`time_limit_ms` 表示每个测试点的 CPU 时限，`memory_limit_mib` 单位为 MiB。读到配置不等于已启用限制；第二阶段结果须如实记录启用情况。编译时间、排队时间与单测试点 CPU 时间分别处理，具体时限口径见总方案。

## 正确结果样例

```json
{
  "task_id":"T1","verdict":"AC","score":100,
  "compile_error":"","error_message":"",
  "test_results":[
    {"case_id":"01","verdict":"AC","exit_code":0,"stdout":"3\n"},
    {"case_id":"02","verdict":"AC","exit_code":0,"stdout":"3\n"},
    {"case_id":"03","verdict":"AC","exit_code":0,"stdout":"0\n"}
  ],
  "execution":{"mode":"local_demo","cpu_limit_enforced":false,"memory_limit_enforced":false}
}
```

## 编译失败结果样例

```json
{
  "task_id":"T3","verdict":"CE","score":0,
  "compile_error":"示例：此处保存编译器实际错误输出","error_message":"",
  "test_results":[],
  "execution":{"mode":"local_demo","cpu_limit_enforced":false,"memory_limit_enforced":false}
}
```

编译失败不运行测试点。`compile_error` 保存编译器错误，`error_message` 保存环境或内部执行错误；`stdout` 是程序实际标准输出，`exit_code` 无法取得时为 `null`。`execution` 按实际工具和限制填写，不能直接照抄示例声称完成隔离。

第二阶段先验证 AC、WA、CE、RE。按配置顺序运行全部测试点；全部通过为 AC，否则整份判定采用第一个失败测试点的判定；通过测试点分数单独累加。输出比对只统一换行符，其余文本精确比较。

内部保护超时分别记 `COMPILE_TIMEOUT`、`RUN_TIMEOUT`；配置或工具失败记 `SYSTEM_ERROR` 并填原因，不冒充程序 RE。编译阶段失败时分数为 0、测试点列表为空；无法建立有效评测时分数为 `null`。CPU 时限的 TLE 和内存限制的 MLE 留到下一阶段验证。

## 模拟数据单独约定

模拟器为任务增加 `arrival_ms`、`service_ms`，输出 `start_ms`、`finish_ms`、`wait_ms`、`turnaround_ms`，统一使用毫秒。入队序号按到达顺序分配，同刻到达按固定顺序。

模拟时钟只向选择器提供已到达任务；服务时长供模拟器推进时钟，FCFS 不用它排序。CSV 增加 `data_source=simulation`，写到 `artifacts/simulation/`。真实四任务演示输出真实判定，不能混入模拟值。

## N2.2 分布式消息字段草案（2026-11-10）

本节用于下一阶段，原单机协议与历史内容保留。**目前只有字段草案和格式样例，尚未接入队列或原生服务；参数与判定口径在 N2.3 统一，Python/C++ 契约在 N2.4 验证。**

### 三类消息

| 消息 | 样例 | 专用字段与含义 |
| --- | --- | --- |
| `compile_task` | [编译任务](../../protocol/golden/compile_task.json) | `source_hash` 源码对象、`test_bundle_hash` 题包快照、`compiler_profile` 服务端预设编译方案、`limits` 编译限制 |
| `eval_task` | [评测任务](../../protocol/golden/eval_task.json) | `bin_hash` 可执行文件对象、题包快照、测试点身份、`target_node_id` 目标节点、运行限制和比较模式 |
| `eval_result` | [结果事件](../../protocol/golden/eval_result.json) | `stage` 编译/评测阶段、`node_id` 实际节点、`verdict` 判定、`reason` 原因、资源记录和错误摘要 |

三份文件都是合成样例。重复的 a/b/c 哈希是格式占位值，不代表已上传对象；编译方案名和限制数值也不代表已启用配置。消息中的对象哈希均指内容 SHA-256（`sha256:` 加 64 位小写十六进制）；构建缓存键与可执行文件内容哈希分开处理，细节留待 N2.3。

### 重点理解的编号

| 字段 | 含义 |
| --- | --- |
| `ver` / `type` | 版本号 / 上述消息种类，首轮版本为 1 |
| `msg_id` | 一条逻辑消息的唯一编号；同一消息重投保留编号 |
| `file_id` | 整份提交的稳定编号；三类消息用它关联 |
| `correlation_id` / `emitted_at` | 日志关联号（等于 `file_id`）/ 该消息产生的 UTC 时间 |
| `enqueue_seq` | 控制面分配的提交入队序号；同一提交的任务保持一致 |
| `test_id` / `sequence` | 测试点字符串编号（保留 `01`）/ 从 1 开始的配置顺序，最终失败点按 `sequence` 确定 |
| `attempt` / `judge_id` | 从 1 开始的执行尝试 / `file_id:test_id:attempt`，区分每点每次执行；同一次投递重试不增加 attempt |

编译任务是一份提交，评测任务是一个测试点。`target_node_id` 是派发目标，结果中的 `node_id` 是实际执行者，二者应一致；具体队列路由在 N2.3 确定。

除下述 CE 空字段外，编号为非空字符串，`file_id/test_id` 不含冒号；`ver/enqueue_seq/sequence/attempt` 为正整数。只有明确重新执行时才增加 `attempt` 并生成新 `judge_id`，同一消息重复投递保持不变。限制值为整数，CPU/墙钟/内存限制大于 0，输出上限允许为 0；实际测量值为非负整数或 `null`，标志字段使用 JSON 布尔值。

### 单位、空值与结果范围

- 所有 `*_ms` 为毫秒，`*_bytes` 为字节；编译与运行各有独立的 `limits`。`limits.out_bytes` 在编译阶段指诊断输出上限，评测阶段指标准输出上限，具体截断/比较规则在 N2.3 确定。
- 结果中的 `cpu_ms`、`wall_ms`、`mem_bytes`、`out_bytes` 是实际采集值；没有采集时填 `null`，不能用 0 代替未知。`memory_source` 标明内存口径，如 `rss_peak` 或 `cgroup_peak`，未知时为 `null`。
- `exit_code` 是被测程序正常返回码；被信号终止或未运行时为 `null`。`signal=0` 表示已知没有终止信号；未知为 `null`。`wall_timeout` 保留实际墙钟超时证据。
- `verdict` 回答“结果是什么”，`reason` 回答“为什么”；`stderr_tail` 保存实际错误输出摘要，`detail` 保存编译诊断/系统错误说明。样例原因枚举的完整映射在 N2.3 定稿。
- 首轮 `judge_mode=normal`、`compare_mode=common`、`evaluation_mode=all_cases`，结果 `short_circuited=false`。提交级聚合仍为全部 AC 才 AC，否则按测试点配置顺序取首个非 AC。
- 单点结果使用 `stage=eval`、`source=runner`。**编译失败不是测试点结果**：使用 `stage=compile`、`source=compiler`、`verdict=CE`；`test_id`、`sequence`、`judge_id` 均为 `null`，`attempt` 仍记录编译尝试，不伪造测试点 01。编译结果按 `(file_id, stage, attempt)` 去重，单点结果按 `judge_id` 去重。

编译任务也携带 `attempt`，编译失败事件原样返回该值。目标节点一致性针对评测任务及其单点结果；编译失败事件的 `node_id` 填实际编译节点。

### 与现有单机接口的边界

新消息不能直接当作现有 TCP 请求。N3 需把对象哈希解析为本机文件，再适配旧的 `bin_path/input_path/answer_path/result_dir`；首轮保留原协议作为基线。

原生结果 `mem_kb` 转字节时乘 1024，并注明 RSS 口径；原生 `exit_code=-1` 转为 `null`，保留真实 `signal`。RE/TLE 提前返回时 `out_bytes` 可能是未统计的默认 0，适配层未读取输出前应记 `null`。目前原生服务没有统一 `reason`，不能仅凭 WA 等判定猜原因；后续依据实际执行证据接入。

## N2.3 第一版实施规则（2026-11-10）

本节确定下一阶段的 v1 规则，冲突处优先于上方历史设计；旧内容保留。机读配置见 [policy_v1.json](../../protocol/policy_v1.json)。**规则已确定，N3 尚未接入，不能据此声称资源限制或队列已启用；N1 基线保持原样。**

### 编译、执行与比较

| 项目 | v1 采用规则 |
| --- | --- |
| 被测程序编译 | `cpp_baseline_v1` 对应 `g++ -std=c++17 -O2 -pipe`，沿用 N1；不采用任意客户端编译参数或 `-march=native` |
| 服务自身 | C++ Worker 使用 C++23，与被测程序的语言版本分开；原 Makefile 不在本步修改 |
| 编译限制 | 目标配置：CPU 10000 ms、墙钟 30000 ms、内存 512 MiB、诊断输出 1 MiB；实际启用情况需 N3 验证，当前 N1 只证实编译墙钟保护配置及语法错误阻断 |
| 评测时限 | 每点独立使用题目 CPU 时限；首轮 `wall_ms=cpu_ms`，沿用基线，二者分别记录 |
| 内存 | 配置由 MiB 转字节；RSS、地址空间和 cgroup 内存口径分别注明，不凭分配失败或 SIGKILL 猜 MLE |
| common 比较 | 统一换行，去行末空格/制表符和文件末尾空行，保留行内空白；长度按归一化后的 UTF-8 字节计算 |
| 输出限制 | 语义上限沿用归一化答案字节数；文件安全上限为 `min(256 MiB, out_bytes + 4096)`；超限为 `WA/OUTPUT_LIMIT`，首轮不单列 OLE |
| 运行与汇总 | 普通题、全部测试点运行；就绪测试点按 `(enqueue_seq, sequence, attempt)` 选择，编译未完成的提交不占评测槽；汇总按配置顺序而非完成先后 |

程序输出与答案按原始字节做上述归一化，不做有损解码；JSON 消息固定使用 UTF-8。配置名 `cpp_baseline_v1` 的参数冻结，今后改变参数需使用新配置名并回归验证。

公开提交接口首轮只接受 `language=cpp17`，由控制面选择 `cpp_baseline_v1`；消息使用配置名，普通提交不能自带任意编译参数。

### 消息封装、路由与编译通知

NATS 载荷采用一条 UTF-8 JSON，不加应用层 4 字节长度前缀；该前缀只用于保留的本机 TCP 适配层。

| 消息 | subject | 处理方 |
| --- | --- | --- |
| 编译任务 | `algoos.compile.task` | 编译 Worker |
| 编译通知/失败 | `algoos.compile.result` | 控制端登记产物或收敛 CE/SE |
| 指定节点评测任务 | `algoos.eval.task.<target_node_id>` | 仅目标评测 Worker |
| 单点评测结果 | `algoos.eval.result` | 控制端聚合器 |

`node_id` 只用字母、数字、下划线和连字符。Worker 的消费者按自己的 subject 过滤，不抢走其他节点任务后丢弃；NATS 的 subject/过滤能力参见 [官方说明](https://docs.nats.io/concepts/subjects)。

编译成功通知仍用 `type=eval_result, stage=compile`，增加 `status=COMPILED`，`verdict=null, reason=NONE`，携带 bin/题包内容哈希、编译配置和入队序号；[成功通知样例](../../protocol/golden/compile_success_result.json)。控制端收到后按冻结的题包清单产生 `eval_task`，编译 Worker 不自行决定评测节点。

编译失败为 `stage=compile, status=FINAL, verdict=CE/SE`，没有测试点身份，也不产生评测任务；[CE 样例](../../protocol/golden/compile_error_result.json)。`COMPILED` 不是提交终态；`stage=eval, status=FINAL` 只表示该测试点本次执行完成。只有聚合器能写整份提交的最终状态。

所有 `stage=compile` 通知的 `test_id/sequence/judge_id` 都为 `null`；未运行被测程序时 `exit_code/signal` 也为 `null`。成功与 CE 样例是互斥场景，不是一次编译同时发生两种结果。

为兼容 N2.2 样例，结果缺少 `status` 时按 `FINAL` 解释；新生产者显式填写。编译通知每个 `(file_id, stage, attempt)` 只接受一种确定结果，单点按当前有效 `judge_id` 去重；已终结提交不被迟到事件改写。处理结果/待发布事件先持久化，再确认消费；这些可靠性行为待 N3 验证，不能把消息重投视为新的执行尝试。

v1 缺必填字段、错误类型、未知版本/消息种类/模式或不支持的编译配置应拒绝；未知的附加字段保留并随往返输出，不作为已支持功能。具体解析检查在 N2.4 实现。

### 判定与原因

| 判定 | 原因与依据 |
| --- | --- |
| AC | `NONE`，正常退出且比较通过 |
| WA | `ANSWER_MISMATCH` 或 `OUTPUT_LIMIT`，分别保留内容不一致、输出超限证据 |
| CE | `COMPILE_ERROR`、`COMPILE_TIMEOUT`、`COMPILE_LIMIT`，仅编译阶段；工具缺失等环境错误为 SE |
| RE | `NONZERO_EXIT` 或 `CRASH_SIGNAL`，且没有更优先的资源超限证据 |
| TLE | `WALL_TIMEOUT` 或 `CPU_TIMEOUT`，区分实际经过时间与 CPU 限制 |
| MLE | `OOM_KILL` 或 `MEM_PEAK_OVER`，必须有明确且口径可比的内存证据 |
| SE | `SYSTEM_ERROR`，例如设置失败、对象缺失、哈希校验失败；不冒充被测程序 RE |

判定优先级：系统设置错误 → 已确认 OOM → 墙钟超时 → CPU 限制信号 → 输出限制信号 → 其他终止信号 → 非零退出 → 可比内存峰值超限 → CPU 时间超限 → 输出长度超限 → 内容比较。单独的 SIGKILL 或退出码 125 不能作为系统/资源错误的充分依据，需结合实际证据；现有原生实现的差异在 N3.1 处理。

未知测量值仍用 `null`；被信号终止的程序 `exit_code=null` 并保留 `signal`。未采集的墙钟耗时、RE/TLE 提前返回的默认输出长度，不填造为真实 0。

### 内容哈希与构建缓存

`source_hash/bin_hash/test_bundle_hash` 均对实际对象字节计算 SHA-256；下载后复核。构建缓存键另对包含源码哈希、不可变编译配置名、编译器版本、目标平台的 JSON 对象做 SHA-256：按键名排序、UTF-8、无额外空白；**缓存键不是 bin 内容哈希**。节点工具链及平台由部署配置记录，缓存不能跨不兼容环境复用。

当前只确定规则并提供合成样例。下一步 N2.4 编写 Python/C++ 契约解析、规范化和往返检查，覆盖缺字段、错误类型、版本、哈希、身份关联及编译/评测分支；实际 Worker、队列确认与资源行为在 N3 验证。

## N2.4 解析边界补充（2026-11-11）

已新增 [Python](../../protocol/codec_v1.py) / [C++](../../protocol/codec_v1.hpp) 检查与规范化实现，原消息样例保留。唯一自动补充为结果缺省 `status=FINAL`；不改编号、不补造测量值，附加字段一起往返。验证命令见 [部署说明](../ops/deployment.md) 末尾。

- 消息严格采用 UTF-8 JSON 对象，拒绝重复键、含 NUL 的键、无效 Unicode、NaN/Infinity；整数统一限定有符号 64 位，布尔值不能充当整数。嵌套深度限制为 64 层以内。
- `emitted_at` 使用真实有效的 UTC 日期，格式 `YYYY-MM-DDTHH:MM:SS[.小数秒]Z`，小数秒最多 6 位。哈希、节点路由名、测试点身份、编译配置、状态与原因按上述 v1 规则检查。
- 内存实测值与 `memory_source` 同时提供或同时为 `null`；编译通知无被测程序退出码/信号。墙钟标志保留实际证据，系统错误或已确认 OOM 可优先于墙钟超时，不清除原标志。

AI 已实测合成消息的解析与往返；尚待 lhy 复跑，未验证队列、执行行为、真实哈希对象或分布式可靠性。
