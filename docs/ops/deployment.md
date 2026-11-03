# AlgoOS 评测守护进程：部署与使用（reroll）

当前入口是根目录的 [test_pipeline.py](../../test_pipeline.py)：

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

当前开发机（见 [docs/env_system.txt](env_system.txt)）：Linux/WSL2、g++ 16.2.1、GNU Make、Python 3.14.7、json-c。

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
| `WA` | 答案不一致，或输出超限（OLE 归入 WA） |
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
- 修改 `problem.json` 字段或 verdict 规则时，同步 [docs/stage1_agreement.md](../specs/stage1_agreement.md) 与相关调用方。

## 相关文件

- [test_pipeline.py](../../test_pipeline.py)
- [judge/problem.py](../../judge/problem.py)
- [judge/pipeline.py](../../judge/pipeline.py)
- [judge/native/eval_server.cpp](../../judge/native/eval_server.cpp)
- [judge/native/README.md](../../judge/native/README.md)
- [problems/lazy_gold/problem.json](../../problems/lazy_gold/problem.json)
- [problems/sabotage/problem.json](../../problems/sabotage/problem.json)
- [docs/stage1_agreement.md](../specs/stage1_agreement.md)
- [progress.md](../records/progress.md)
