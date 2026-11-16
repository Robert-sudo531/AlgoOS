> **历史文档**：本文描述当时状态，节点数量、目录和判定口径可能已变化；当前入口见 [docs/README.md](../../README.md)。

> **历史文档（2026-11-16 注）**：这里记录旧单机 `reroll` 链路与 N4 接手说明。新的分布式集群部署、systemd、cloud-init 与 KVM 多节点参考见 [集群部署](../../operations/cluster_deployment.md)；新架构见 [分布式架构](../../design/distributed_architecture.md)。

# AlgoOS 评测守护进程：部署与使用（reroll）

当前入口是根目录的 test_pipeline.py：

```bash
python3 test_pipeline.py
```

它启动时扫描 `problems/`，之后保持前台运行，监听 `submissions/`；收到 `编号__题目ID.cpp` 后编译、评测，并把结果追加到 `result/result.tsv`。C++ 评测服务端由它自动启动。

---

## 1. 架构与数据流

```text
problems/<problem_id>/problem.json + tests/
        │ 启动时加载
        ▼
test_pipeline.py（Python 主控，前台常驻）
        │ 编译 submissions/<stem>.cpp
        │ 启动 / 停止
        ▼
judge/native/eval_server（C++ 单进程 + 线程池）
        │ fork / exec / wait4
        ▼
submissions/<stem>              被测程序
submissions/<stem>__result/     实际 .out/.err
        │ 单测试点 verdict
        ▼
result/result.tsv               每个提交一行
```

职责：

- Python：加载题目、编译、构造请求、ACM 聚合、写 `result.tsv`、前台轮询。
- C++：单测试点执行、资源限制、common 比对、单点 verdict。
- 题目标准答案只读 `problems/.../tests/*.out`；实际输出写 `submissions/<stem>__result/`。

---

## 2. 环境与构建

当前开发机（见 [docs/env_system.txt](environment_record.txt)）：Linux/WSL2、g++ 16.2.1、GNU Make、Python 3.14.7、json-c。

```bash
cd /home/AlgoOS
make -C judge/native
# 产物：judge/native/eval_server
```

ASan/UBSan 版本：

```bash
make -C judge/native sanitize
# 产物：judge/native/eval_server_asan
```

依赖缺失时：Arch `pacman -S json-c`；Debian/Ubuntu `apt install libjson-c-dev`。执行 `sudo` 前确认。

---

## 3. 目录布局

```text
项目根目录/
├── test_pipeline.py                 # 唯一前台入口
├── judge/
│   ├── problem.py                   # problem.json 模型与派生 out_bytes
│   ├── pipeline.py                  # 通用流程：编译、Socket、聚合、result.tsv、轮询
│   └── native/
│       ├── eval_server.cpp          # C++ 评测服务端源码
│       ├── eval_server              # make 构建产物（同目录）
│       ├── eval_server_asan         # make sanitize 产物（同目录）
│       ├── Makefile
│       └── README.md                # 协议与 C++ 实现说明
├── problems/
│   ├── lazy_gold/
│   │   ├── problem.json
│   │   └── tests/1.in 1.out ...
│   └── sabotage/
│       ├── problem.json
│       └── tests/1.in 1.out ...
├── submissions/                     # 运行时输入/输出，.gitignore 忽略内容
│   ├── 00000001__lazy_gold.cpp
│   ├── 00000001__lazy_gold
│   └── 00000001__lazy_gold__result/
│       ├── 1.out
│       ├── 1.err
│       └── ...
├── result/
│   ├── .gitkeep
│   └── result.tsv                   # 每个提交一行
└── work/                            # 其他临时文件
```

`submissions/` 是运行时目录，不提交内容；`result/result.tsv` 是运行日志，保留供查看。

---

## 4. problems/<problem_id>/problem.json

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

- `compare_mode` 只有 `common` / `special`。
- 不记录 `score`；ACM 赛制，全部测试点 AC 才 AC，否则第一个非 AC。
- `time_limit_ms` 同时作为 CPU 与 wall 时限。
- `memory_limit_mib` 转字节后作为 `RLIMIT_AS`。
- `tests[].input/output` 相对 `problem.json` 所在目录。
- 每个测试点的 `out_bytes` 由标准答案 `trim_common` 后的字节数自动派生，不写在 JSON 里。
- legacy 字段 `score`、`output_limit_kib`、`wall_time_limit_ms` 被忽略。

common 比较：CRLF/CR→LF；去掉每行末尾空格/制表符；去掉文件末尾空行；然后严格相等。

special：本期只保留枚举；C++ 直接返回 SE（checker 未实现）。

---

## 5. submissions 命名与结果目录

```text
submissions/
├── 00000001__lazy_gold.cpp          # 提交源码：编号__题目ID.cpp
├── 00000001__lazy_gold              # 编译出的同名可执行文件
└── 00000001__lazy_gold__result/
    ├── 1.out                       # 实际输出
    ├── 1.err
    ├── 2.out
    └── 2.err
```

- 文件名必须是 `<submission_id>__<problem_id>.cpp`。
- `stem` = 去掉 `.cpp` 的文件名。
- 一份提交只编译一次，所有测试点共用同一个可执行文件。
- 结果目录 `submissions/<stem>__result/` 由 Python 创建，C++ 写入 `<case_id>.out/.err`。
- 标准答案从 `problems/<problem_id>/tests/<case_id>.out` 读取。
- 同一 `stem` 重跑会覆盖二进制和对应 `.out/.err`；需要独立记录时换提交编号。

示例源码：

```cpp
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << "\n";
    return 0;
}
```

放入 `submissions/00000003__lazy_gold.cpp` 后，守护进程会自动处理。

---

## 6. 启动、停止与前台运行

```bash
python3 test_pipeline.py
```

输出示例：

```text
[test_pipeline] problems=lazy_gold,sabotage
[test_pipeline] server=127.0.0.1:47209 watching /home/AlgoOS/submissions
[test_pipeline] 00000001__lazy_gold.cpp -> AC (11/11)
[test_pipeline] 00000002__sabotage.cpp -> AC (14/14)
```

- 启动时先处理 `submissions/` 中已有的 `.cpp`（按文件名排序），之后每 0.5 秒轮询新文件。
- `Ctrl-C` 停止轮询并终止 C++ 服务端。
- `--server` 指定服务端路径；`--port` 指定端口（0 = 自动空闲端口）；`--workers` 指定评测线程数。

```bash
python3 test_pipeline.py --server judge/native/eval_server --workers 4
```

### systemd 用户服务（可选）

> WSL2 需先启用 systemd；未启用时用前台运行。

`~/.config/systemd/user/algoos-pipeline.service`：

```ini
[Unit]
Description=AlgoOS test pipeline
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/AlgoOS
ExecStart=/usr/bin/python3 /home/AlgoOS/test_pipeline.py --workers 4
Restart=on-failure
RestartSec=1

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now algoos-pipeline.service
journalctl --user -u algoos-pipeline.service -f
```

---

## 7. result/result.tsv

Tab 分隔、UTF-8、首行表头；每个提交追加一行：

```text
timestamp	source_file	problem_id	submission_id	verdict	passed	total	cases	elapsed_ms	message
2026-11-03T22:02:13	00000001__lazy_gold.cpp	lazy_gold	00000001	AC	11	11	1=AC;2=AC;...	711
```

| 列 | 含义 |
| --- | --- |
| `timestamp` | 处理完成时间 |
| `source_file` | 提交文件名 |
| `problem_id` / `submission_id` | 从文件名解析 |
| `verdict` | 提交级 ACM 判定 |
| `passed` / `total` | AC 测试点数 / 总数 |
| `cases` | 每个测试点的判定，`;` 分隔 |
| `elapsed_ms` | 从开始处理到写结果的实际耗时 |
| `message` | CE 编译摘要或 SE 错误；其余为 `-` |

单测试点的实际输出和 stderr 在 `submissions/<stem>__result/`。

---

## 8. 判定与限制

### 判定

| verdict | 含义 |
| --- | --- |
| `AC` | 所有测试点通过 |
| `WA` | 答案不一致；输出超限单独为 `WR` |
| `TLE` | wall/CPU 超时 |
| `MLE` | 内存超限 |
| `RE` | 非零退出或崩溃信号 |
| `CE` | 编译失败 |
| `SE` | 服务端/协议/未实现 special 等系统错误 |

提交级 ACM 聚合：全部 AC 才 AC；否则按测试点顺序取第一个非 AC。

### 限制

| 字段 | 含义 |
| --- | --- |
| `cpu_ms` | CPU 时限；`RLIMIT_CPU` 向上取整到秒 |
| `wall_ms` | 实际经过时间；等于 `cpu_ms` |
| `mem_bytes` | `RLIMIT_AS` |
| `out_bytes` | trim 后输出语义上限；标准答案 trim 后大小 |
| `fsize_cap` | 磁盘安全上限：`min(256 MiB, out_bytes + 4 KiB)` |

- trim 后实际输出超过 `out_bytes` → WA。
- 原始输出超过 `fsize_cap` 或 `SIGXFSZ` → WA。
- 正确输出末尾多一个换行不会被 `RLIMIT_FSIZE` 误杀；安全 cap 留 4 KiB 余量。
- 消息上限 1 MiB；答案读取上限 64 MiB；stderr 尾部最多 4 KiB。

---

## 9. 验证

```bash
make -C judge/native
python3 test_pipeline.py
```

另开终端放入一个提交：

```bash
cp /path/to/source.cpp submissions/00000003__lazy_gold.cpp
```

观察 `result/result.tsv` 追加一行，单测试点实际输出在
`submissions/00000003__lazy_gold__result/`。`Ctrl-C` 停止守护进程。

ASan/UBSan 版本仅用于调试，可直接替换服务端（正常部署不需要）：

```bash
make -C judge/native sanitize
python3 test_pipeline.py --server judge/native/eval_server_asan
```

---

## 10. 常见问题

### 服务端提前退出

先确认 `judge/native/eval_server` 存在并重新构建：

```bash
make -C judge/native
```

### CE 很多

查看 `result.tsv` 的 `message` 和编译日志；先手动编译：

```bash
g++ -std=c++17 -O2 -pipe -o /tmp/test submissions/xxx__yyy.cpp
```

### TLE / WA 与预期不符

- 检查 `submissions/<stem>__result/<case_id>.out` 与 `problems/<id>/tests/<case_id>.out`。
- 注意 common 只忽略行末空格和末尾空行，内部空格必须一致。
- `wall_ms = time_limit_ms`，不是额外的更长时间。

### special 返回 SE

本期 special 只有枚举，未实现 checker；这是预期行为。

### result.tsv 被覆盖

`result.tsv` 只追加，不覆盖；清理文件需手动处理。每次独立实验建议保留独立副本。

---

## 11. 生产交接与限制

- 生产版状态保存、Web 接口、用户提交管理仍由系统负责人维护；当前 `test_pipeline.py` 是本地前台原型。
- special checker、cgroup/Isolate、鉴权、TLS、跨重启队列未实现。
- `submissions/` 是运行时目录，不提交；`result/result.tsv` 作为运行日志保留。
- 修改 `problem.json` 字段或 verdict 规则时，同步 [docs/stage1_agreement.md](../coursework/stage1_agreement.md) 与相关调用方。

## 相关文件

- test_pipeline.py
- judge/problem.py
- judge/pipeline.py
- judge/native/eval_server.cpp
- judge/native/README.md
- [problems/lazy_gold/problem.json](../../../problems/lazy_gold/problem.json)
- problems/sabotage/problem.json
- [docs/stage1_agreement.md](../coursework/stage1_agreement.md)
- [progress.md](../../records/progress.md)

## 2026-11-10 补充：N1.1 最小正常评测

从项目根目录操作。先在本机终端安装构建依赖（sudo 密码由本人在终端输入）：

```bash
sudo apt-get install libjson-c-dev
pkg-config --modversion json-c
```

版本查询成功后运行：

```bash
python3 -m tests.n1_ac --actor lhy
```

入口自动构建现有 C++ 服务，把 [正确 A+B 源码](../../../examples/a_plus_b.cpp) 复制到独立提交目录，通过现有 `Pipeline` 处理 [最小题包](../../../problems/a_plus_b/problem.json)，验证后退出。`--actor` 填实际执行人。

- 预期：两组实际输出分别为 `3\n`、`-5\n`，显示 `N1.1 正常提交验证通过：AC 2/2`，脚本退出码为 0。
- 临时构建与运行文件：`work/n1_ac_<编号>/`；结果与日志：`artifacts/system/n1_ac_<编号>/`。每次运行创建新目录，结束时打印证据位置。
- `manifest.json` 保存执行人、源码哈希和结果；`build.txt` 是构建日志，`verify.txt` 是验证日志；评测成功运行后另有 `result.tsv` 和实际输出。
- 本步骤只验证正确提交。错误样例、完整隔离和分布式能力另行验收；上面的旧操作说明保留作参考。

## 2026-11-10 补充：N1.2 错误答案 WA

在项目根目录运行：

```bash
python3 -m tests.n1_wa --actor lhy
```

[错误样例](../../../examples/wrong_answer.cpp) 故意输出 `b - a`，沿用原题包和标准答案。预期两组实际输出为 `1\n`、`11\n`，程序退出码均为 0、输出未超限，汇总为 WA、0/2 通过。

验证脚本退出码 0 表示“正确识别了 WA”。每次在 `artifacts/system/n1_wa_<编号>/` 保留独立记录，`responses.json` 是真实单点响应；正常 AC 样例与历史记录均保留。

## 2026-11-10 补充：N1.3 编译错误 CE

在项目根目录运行：

```bash
python3 -m tests.n1_ce --actor lhy
```

[错误源码](../../../examples/compile_error.cpp) 的 `return 0` 故意缺少分号。入口调用真实编译和提交处理流程，不启动原生评测服务；若编译失败后仍调用测试点评测，检查会报错。

预期看到编译诊断、`编译成功：False`、`是否调用测试点评测：False` 和 `N1.3 编译错误验证通过：CE，未运行测试点`。验证脚本退出 0 表示 CE 检查通过，错误源码本身编译失败。独立证据保存在 `artifacts/system/n1_ce_<编号>/`，`compile.txt` 为完整编译诊断。

## 2026-11-10 补充：N1.4 运行错误 RE

在项目根目录运行：

```bash
python3 -m tests.n1_re --actor lhy
```

[样例](../../../examples/runtime_error.cpp) 正常计算 A+B、写入错误提示后执行 `return 7`。预期两组输出仍为 `3\n`、`-5\n`，程序退出码均为 7，汇总 RE 0/2，最后显示 `N1.4 运行错误验证通过`。

验证脚本退出 0 表示按预期识别了 RE。证据保存在独立的 `artifacts/system/n1_re_<编号>/`，包含实际输出和真实单点响应。本次只验证主动非零退出，崩溃信号未单独验证。

## 2026-11-10 补充：N1.5 墙钟超时 TLE

在项目根目录运行：

```bash
python3 -m tests.n1_tle --actor lhy
```

[样例](../../../examples/time_limit.cpp) 先输出并刷新 A+B，再休眠 3 秒；现有题包的墙钟限制为 1000 毫秒。预期两点均 TLE、`墙钟超时=True`、终止信号为 9，保留超时前的 `3\n`、`-5\n`，汇总 TLE 0/2。

验证脚本退出 0 表示检查通过；原生响应中的退出码 -1 表示被信号终止。独立证据在 `artifacts/system/n1_tle_<编号>/`。本步骤只验证实际经过时间保护，未单独验证 CPU 超限或子孙进程清理。

## 2026-11-11 补充：N2.4 Python/C++ 消息契约

沿用已有 Python、g++、pkg-config 与 libjson-c-dev，从项目根目录运行：

```bash
python3 -m tests.n2_contract --actor lhy
echo $?
```

入口 自动编译独立的 C++ 消息检查程序，核对 23 组合法消息的双向往返，以及 58 组错误输入的双端拒绝。预期显示“N2.4 Python/C++ 契约验证通过”，退出码 0；无需修改样例。

构建放在 `work/n2_contract_<编号>/`，独立证据放在 `artifacts/system/n2_contract_<编号>/`：`manifest.json` 记录执行人、源码哈希和结果；`build.txt`、`verify.txt` 保存日志；`checks.json` 保存实际输入及每项检查结果。本步使用合成消息，不启动评测服务或队列。

## 2026-11-11 补充：N3.1 第一步，真实 AC 结果适配

沿用已有依赖，在项目根目录运行：

```bash
python3 -m tests.n3_ac_adapter --actor lhy
echo $?
```

入口 自动构建原 C++ 服务、编译并运行正确 A+B，再将真实响应转换成 v1 结果。预期两点显示 `AC/NONE`，实际输出分别为 `3\n`、`-5\n`，最后显示“N3.1 第一步验证通过”，退出 0。

运行使用本机 TCP；临时文件在 `work/n3_ac_adapter_<编号>/`，证据在 `artifacts/system/n3_ac_adapter_<编号>/`。重点看 `responses.json`（原生响应）和 `v1_results.json`（新格式）：内存从 KiB 换成字节，`wall_ms=null` 表示未采集；其他日志沿用原结构。本步只适配 AC，尚未接收分布式任务或连接队列。

## 2026-11-11 补充：N3.1 WA 内容错误适配

在项目根目录运行：

```bash
python3 -m tests.n3_wa_adapter --actor lhy
echo $?
```

入口 复用已有减法样例和 C++ 服务。预期两点显示 `WA/ANSWER_MISMATCH`、退出码 0、输出未超限；实际为 `1\n`、`11\n`，预期为 `3\n`、`-5\n`。最后显示“N3.1 WA 内容错误适配验证通过”，验证脚本退出 0 表示按预期识别了错误答案。

临时文件和独立证据分别位于 `work/n3_wa_adapter_<编号>/`、`artifacts/system/n3_wa_adapter_<编号>/`。原生响应、新格式仍看 `responses.json`、`v1_results.json`；`guards.json` 单独记录 5 项合成防误用检查，不代表原生输出超限已实测。当前入口只适配 common 内容错误，其他 WA 原因后续接入。

## 2026-11-12 补充：N3.1 C++ 本地核心 AC/WA

在项目根目录运行：

```bash
python3 -m tests.n3_local_core --actor lhy
echo $?
```

验证入口 构建 `eval-local` 目标，编译原有 A+B 正确/错误样例，各跑两个测试点。预期正确样例为 `AC/NONE`，错误样例为 `WA/ANSWER_MISMATCH`，最后显示“N3.1 C++ 本地核心验证通过：AC 2/2，WA 0/2”，退出 0。

临时产物在 `work/n3_local_core_<编号>/`，证据在 `artifacts/system/n3_local_core_<编号>/`，`cases.json` 保存任务、实际输出与 v1 结果。入口从标准输入读任务，本机文件由测试脚本传入，不启动 TCP 服务。本步验证正常退出的 AC/WA；异常结果、下载和内容校验、正式 Worker 发布包仍待后续。

## 2026-11-12 补充：N3.1 墙钟超时 TLE

在项目根目录运行 `python3 -m tests.n3_wall_timeout --actor lhy`。验证入口 构建本地核心，回归 AC/WA/RE/SE，再用原休眠样例执行两个测试点。预期两点均为 `TLE/WALL_TIMEOUT`、信号 9，保留超时前输出；末尾显示“墙钟超时验证通过”，脚本退出 0。

证据在 `artifacts/system/n3_wall_timeout_<编号>/`，`cases.json` 包含超时结果、实际耗时和输出。本步验证 1000 ms 墙钟时限，CPU 超限另行验证。

## 2026-11-12 补充：N3.1 崩溃信号 RE

在项目根目录运行 `python3 -m tests.n3_crash_signal --actor lhy`。入口 回归已有判定，并实测 `SIGABRT`。预期为 `RE/CRASH_SIGNAL`、信号 6、退出码为空、墙钟未超时，保留崩溃前输出；最后显示“崩溃信号验证通过”，脚本退出 0。结果见 `artifacts/system/n3_crash_signal_<编号>/cases.json`。

## 2026-11-12 补充：N3.1 本地判定批次

在项目根目录运行 `python3 -m tests.n3_local_batch --actor lhy`。入口 一次构建核心，汇总 AC/WA、RE/SE、墙钟超时、崩溃及两种输出超限。末尾报告 12 个用例、2 项输入检查通过，退出 0；证据目录中的 `manifest.json` 保存各组日志，`cases.json` 保存实际结果。可用这条命令集中复跑，旧单项入口保留。

## 2026-11-12 补充：N3.1 资源判定批次

在项目根目录运行 `python3 -m tests.n3_local_batch --resources --actor lhy`，在原批次上增加 CPU、地址空间、RSS、诊断摘要和合成内存规则。预期 17 个实际用例、2 项输入检查、7 项合成检查通过。CPU 软限制及实测超限需要约 2 个可用 CPU，硬限制需要约 3 个；容量不足单独标记跳过。真实 cgroup OOM 不在本批验收范围。

## 2026-11-12 补充：本地评测 Worker 归档

在项目根目录运行 `python3 -m tests.n3_local_batch --resources --delivery --actor lhy`。预期 34 个实际用例、6 项输入检查、7 项合成检查通过；`manifest.json` 的 `delivery` 记录归档路径、哈希及节点检查。CPU 条件不足的组单独保留待验收。

单独构建归档用 `make -C judge/native eval-worker-package`，默认写入 `work/eval-worker-build/packages/`；再次打包可指定新的 `WORKER_BUILD_DIR`，同名归档不覆盖。解包后用 `bin/eval-worker check conf/eval-worker.example.json` 检查配置。`run CONFIG BINARY INPUT ANSWER RUN_NAME` 从标准输入读一条 v1 任务、输出一条结果后退出；配置中的相对路径以配置文件目录为基准，运行名用于创建新的工作目录。包内 `manifest.json` 列出所需系统运行库。本轮验收为同机源码外运行，跨机和队列服务留待后续。

## 2026-11-13 补充：N3.2 基础服务批次

从项目根目录以普通用户运行：

```bash
bash scripts/setup_n32.sh && work/n32_runtime/venv/bin/python -m tests.n32_services --actor lhy
```

依赖齐备时第一段直接返回。新 Ubuntu 26.04 x86_64 环境首次需从已配置的 Ubuntu 软件源、MinIO 官方仓库、Go 模块代理与 PyPI 下载和构建；网络中断后可重新运行，复用已完成步骤。MinIO 按 [官方发布标签](https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z)构建。其他本机安装可通过 `--config` 传入与 local.json 同结构的二进制路径配置。

探针仅使用回环地址与临时数据目录，自行启动和停止服务。预期末尾显示“N3.2 基础服务联通验证通过”；`artifacts/system/n32_services_<编号>/` 保存脱敏日志、v1 任务和提交回执。这只验收本机服务协作，尚未接正式编译或评测 Worker。

## N4 应用负责人接手入口（2026-11-15）

先核对交接分支、提交号和 `git status --short`。源码归档可单独复现下方批次；正式协作用双方确认的仓库提交。当前开发目录有尚未提交的源码，不能假定远端已有。归档只含本地验收所需文件，完整历史与原始记录请在主仓库查阅。

```bash
bash scripts/setup_n32.sh && python3 -m tests.n4_handoff --actor 本人姓名
```

从仓库或交接包的根目录执行。首次准备依赖的适用环境与下载说明见上节。批次打出源码归档、在仓库外重新构建和复跑，复用刚准备的运行依赖；临时服务自动退出。成功后保留 `artifacts/system/n4_handoff_<编号>/` 下的 `manifest.json`、`replay.txt` 和 `correctness.csv`。对照表应有 AC、WA、CE、SE 四行，评测任务数分别为 2、2、0、0；重投和去重见 `replay/manifest.json`。失败时回传首个报错和证据路径。

应用负责人先独立复现，再确认云环境的 OS/架构、工具链与运行库、专网、数据盘、虚拟化条件和预算。首轮按控制与基础服务、编译节点、两个评测节点整理角色和连接清单；用干净目录逐角色验收。当前只有本机批次和一次性 Worker；跨机连接配置、常驻进程启动停止和生产隔离需补齐后再验收。Web 只需先定提交、按 ID 查询、显示单点与最终结果三项；接口形状以实际交付为准。当前尚无可供页面调用的正式 HTTP API。

给接手 AI 的任务说明可直接用：「先读 README、本文 N4 交接节、newplan 的状态表。核对当前提交并复跑交接批次。按组记录通过、失败或跳过，保留历史记录。下一批只做一项缺失的部署或接口验收，复用 v1 契约；不要把本机证据写成云端或性能结果。」

### 本机 Web 演示（2026-11-15）

在 N4 复跑后，从项目根目录运行 `python3 -m scripts.demo_web`，打开 `http://127.0.0.1:8765/`；端口冲突可加 `--port 8766`。默认读取最近通过的本机交接记录；也可用 `--evidence <manifest.json>` 指定一次已通过的交接或评测批次。应用负责人只需运行和演示，见接手清单。

- 默认显示真实记录回放，可按提交编号查询结果；AC/WA 各显示两个节点，CE/SE 无评测点。
- 「运行内置样例」会实际重跑本机双节点批次，成功后更新证据号和结果；它只运行仓库内置样例。预录演示时建议先跑一次。
- 本机 HTTP：`GET /api/submissions` 列表，`GET /api/submissions/<id>` 详情，`POST /api/demo/run` 空请求启动内置批次。Ctrl+C 停止服务；批次正在运行时等待它结束。此接口为本机演示，不开放任意源码提交。

竞赛题区展示 [USACO 2014 年 3 月赛 Gold《The Lazy Cow》](https://usaco.org/index.php?cpid=418&page=viewproblem2)。题包的 11 组输入、答案与本地限制从旧提交 `3414b3a` 恢复，参考程序是新写的。启动页面前可用一条命令生成当前版本的竞赛题记录：

```bash
work/n32_runtime/venv/bin/python -m tests.n34_eval_batch --lazy-gold --actor lhy
```

成功后刷新页面。竞赛题区读取最近通过的 `n4_lazy_gold_*` 批次；「运行内置样例」按钮仍跑 A+B。页面的单点墙钟、CPU、峰值 RSS 和各节点完成点数各有独立口径，不能据此推断吞吐或提速。

## 2026-11-16 新版分支：本机最小闭环复跑

在已准备好依赖的本机，从项目根目录运行：

```bash
bash deploy/scripts/local_cluster.sh smoke --actor lhy
```

入口复用 `work/n32_runtime/` 的三项基础服务，另启动 Redis、两个 C++ 评测节点及新版控制面、代理与 API。首次准备需要锁文件的 Python 环境（`UV_PROJECT_ENVIRONMENT="$PWD/work/refactor-venv" uv sync --locked`）、C++ 构建（`cmake -S cpp -B work/refactor-native && cmake --build work/refactor-native`）以及 Redis（系统 `redis-server` 或用 `--redis-bin`、`--redis-lib` 指定本机已解包的程序和库）；三项基础服务可按 N3.2 说明准备。

每次使用临时数据和回环端口，退出后停止进程；预期 `doctor`、迁移和题包导入通过，并依次得到 AC、CE、WA、RE、TLE、SE。证据在 `artifacts/system/refactor_smoke_<编号>/`。本入口只跑可信自带样例，非 root 本机模式不验收生产沙箱。它是无 Docker 的本机验收入口；Compose 启动、浏览器演示和跨机部署另行验收。
