# clone 后的本机环境准备

本页说明其他成员拿到仓库后需要在本机准备哪些目录、工具和配置。所有命令从各自的项目根目录执行；项目根目录是 clone 得到的目录，不必与系统负责人的绝对路径相同。

截至 2026-10-12：S3 的普通 A+B 演示已通过；系统机器上的 Isolate 2.7 已完成安装、配置检查、初始化、第一组 A+B 普通模式运行及清理验证（S4）。cgroup 模式、CPU/内存限制及超时触发行为仍待验证。实际证据见 [开发记录](../progress.md) 与 [系统环境记录](env_system.txt)。

## 哪些内容能从仓库获得

GitHub 上已提交并推送的版本才是其他成员 clone 或更新时获得的内容。本机尚未提交的修改不会自动同步。

| 内容 | 获取与准备方式 |
| --- | --- |
| 项目源码、题目 `.in/.out`、Todo、共同约定、环境记录和 `AGENTS.md` | 随已提交并推送的仓库版本获得。环境记录描述执行者的机器，不代表接收者已完成本机验证。 |
| `artifacts/` 中已提交的小型日志、JSON、CSV 等证据 | 随仓库获得，保留来源；其他成员复现时另存结果，不覆盖已有证据。 |
| `work/.gitkeep` | 随仓库获得，用于保留 `work/` 目录。 |
| `work/stage1/`、A+B 可执行文件及其他临时运行目录 | 被忽略，按运行说明在本机创建并重新编译。 |
| `work/isolate/` 第三方源码、其内部 Git 数据与构建产物 | 被忽略；只有需要独立运行 Isolate 的成员才重新获取指定版本并构建。 |
| `/usr/local/bin/isolate`、`/usr/local/sbin/isolate-cg-keeper`、系统配置、服务文件和 `/var/local/lib/isolate/` 沙箱目录 | 位于仓库之外，不随 clone 获得；按本机条件安装、配置和生成。 |
| Python、g++、Git、apt 开发依赖等工具 | 不随仓库获得，按个人环境清单检查，缺什么补什么。 |
| 虚拟环境、Python 缓存、本地编辑器/Codex 配置与 `.env` | 被忽略，按本机需要准备。当前 S3 不依赖虚拟环境或 `.env`；以后引入项目依赖时再维护依赖清单，需要环境变量时提供不含秘密的 `.env.example`。 |

忽略规则见 [.gitignore](../.gitignore)。题目 `.out` 是标准答案，运行证据在 `artifacts/`，都不因 `.out` 扩展名被统一排除。

## 各成员当前需要什么

| 成员与工作 | 当前环境要求 |
| --- | --- |
| 算法负责人：FCFS、模拟时钟、指标计算 | Python 与 Git；先按 [算法 A1](../algorithm_list/todo_stage1_algorithm.md) 验证自己的 WSL 环境。模拟任务不依赖 g++ 或 Isolate。 |
| 应用与测试负责人：题目包、检查表及示例页面 | 按 [应用 P0](../application_list/todo_transition_application.md) 记录实际工具与投入；这些文件准备任务不要求先安装 Isolate。 |
| 任一成员：独立复现当前 S3 普通演示 | 已验证的 Linux 环境、Python 3、g++ 和仓库中的源码/两组测试文件。 |
| 任一成员：独立复现 Isolate 隔离执行 | 在自己的 Linux 环境另外安装并核对 Isolate、权限和配置；WSL 环境也须本人验证。正式执行限制验证与性能实验仍按方案在系统机器进行。 |

应用 P4 的单提交入口与算法的真实批量联调入口尚待对应 Todo 实现；现阶段先复现 S3，不能把文档中的后续命令当作已有功能。

## 先复现已通过的 S3

在自己的 Linux 项目根目录检查工具并运行：

```bash
python3 --version
g++ --version
mkdir -p work/stage1
python3 -m scripts.run_demo
```

- `mkdir -p` 创建 clone 后缺少的临时目录；当前脚本不会自行创建它。
- 脚本会重新编译 `examples/a_plus_b.cpp`，再读取 01/02 两组测试。当前脚本只使用 Python 标准库，无需先安装 pip 包或 Isolate。
- 核对编译退出码为 0、两组程序退出码均为 0、实际与预期输出均为 `3\n`、两次“输出一致”均为 `True`。当前脚本打印比较结果，不会仅因输出不同而返回失败状态，因此不能只看整个脚本的退出码。
- [stage1_demo.txt](../artifacts/system/stage1_demo.txt) 是系统机器已有的运行证据。其他成员保存新结果时使用独立的 `artifacts/system/<run_id>/` 目录，并在本人环境/开发记录中注明机器、版本、命令和结果位置。

## 需要 Isolate 时再准备

以下整理系统机器已通过的安装及配置检查步骤，适用于 Ubuntu 的首次准备；其他机器仍需独立验证。系统机器另已完成第一组 A+B 的普通模式运行，资源限制验证仍待进行。当前来源是 [官方 Isolate 仓库](https://github.com/ioi/isolate)，版本 2.7，固定提交为 `8f185bb37f3f23e29b33b0c7727c91c13429abe3`。

先检查构建依赖，仅补缺项：`gcc`、`make`、`pkg-config`、`libcap-dev`、`libseccomp-dev`、`libsystemd-dev`。系统机器的实际版本与安装输出见 [环境记录](env_system.txt)。

首次下载时，`work/isolate/` 应尚不存在。在项目根目录执行：

```bash
git clone https://github.com/ioi/isolate.git work/isolate
git -C work/isolate checkout --detach 8f185bb37f3f23e29b33b0c7727c91c13429abe3
make -C work/isolate isolate isolate-cg-keeper default.cf systemd/isolate.service
```

已有目录时先核对其来源、提交与本地改动，不覆盖已有内容。下载和编译只得到本机临时产物，还需要系统安装。

Ubuntu 首次安装、且系统中尚无需保留的 Isolate 安装或配置时，按当前官方规则执行：

```bash
sudo make -C work/isolate install
/usr/local/bin/isolate --version
ls -l /usr/local/bin/isolate
```

当前安装规则会复制二进制、辅助程序、默认配置和服务文件。系统机器已确认版本 2.7、二进制 root/root 归属与 4755 权限。其他机器有现有安装时先核对并备份；版本查询通过仍须继续检查配置。

系统配置位于 `/usr/local/etc/isolate`，不在仓库中。当前默认模板使用 `subid_user=isolate`，需要相应的 subordinate UID/GID 范围；模板也支持显式 `first_uid`、`first_gid`、`num_boxes`。每台机器先检查候选编号是否被现有用户、组或 subordinate ID 范围占用，再选择一种配置方式，并备份原配置。

系统机器采用 UID/GID 60000、`num_boxes=1`，这是该机器核查后的配置，其他成员不能未经核查照搬。编辑后运行：

```bash
/usr/local/bin/isolate --check-config
```

通过应为退出码 0、无错误输出。需要 cgroup 模式时另行核对系统服务与 cgroup 委派。

2026-10-12，系统机器已在不带 `--cg` 的普通模式下完成初始化与第一组 A+B 运行，设置 `--wall-time=5`，得到 [实际输出](../artifacts/system/stage1_isolate.actual.out) `3\n` 和 [执行元数据](../artifacts/system/stage1_isolate.meta) `exitcode:0`。随后执行 `/usr/local/bin/isolate --box-id=0 --cleanup` 返回 0，沙箱目录已移除，仓库内的两份证据仍完整。完整命令见环境记录。正常程序运行通过不代表 CPU/内存限制、超时触发行为或完整受限评测已验证。

## 本人记录与后续说明

算法负责人按清单维护 `docs/env_algorithm.txt`，应用负责人维护 `docs/env_application.txt`，只记录本人实际检查的条件与结果。缺少运行条件时记录具体缺项，按分工先做可独立推进的任务，真实评测可与系统负责人在已验证机器共同复现。

本页集中说明不随 clone 获得的环境与临时文件；系统机器的历史检查仍保留在 `env_system.txt`。后续 S2.7 的阶段复现说明引用本页，再补充已实现的单提交/批量命令及真实证据，不重复维护多套安装步骤。
