# 租用服务器上的 AlgoOS × DOMjudge 性能实验方案

> 适用版本：当前 `reroll` 单机链路（阶段 0/1）与后续分布式主线（阶段 2）。
> 目标：在租用的服务器上部署可控的评测节点，用 DOMjudge 9.0.1 作为成熟工程对照，得到可复现的吞吐、延迟、资源利用率和 verdict 一致性数据。
> 本文与 [distributed_implementation_plan.md](distributed_implementation_plan.md) 第 7、9 节配套；部署细节以本文为准。

---

## 0. 结论先行

1. **先做单机对比，再做分布式对比。** 当前可运行的 AlgoOS 是单机链路，最公平的第一组实验是「AlgoOS 单机 N workers」对「DOMjudge 单 judgehost / N judgehost」，跑在**同一台评测 VM** 上，顺序执行、互不同时运行。
2. **优先租多台云主机，而不是在一台云主机里跑 KVM。** 绝大多数普通 ECS/CVM 不暴露嵌套虚拟化，KVM 起不来；「一台服务器 + 多台 KVM」只在裸金属或明确支持嵌套虚拟化的机型上可行。租 3 台小实例通常比租 1 台大服务器更便宜、更稳，也更接近真实分布式。
3. **公平性有三个必须先解决的隐患：**
   - DOMjudge 的 C++ 编译脚本默认是 `g++ -x c++ -Wall -O2 -static -pipe`，AlgoOS 是 `g++ -std=c++17 -O2 -pipe`；不统一编译参数和 GCC 版本，编译耗时和运行耗时都不可比。
   - DOMjudge 在 chroot + cgroup 中运行，AlgoOS 当前是 `rlimit + 进程组`；这是系统设计差异，必须同时报告「端到端」和「纯评测」两组时间，不能只比一个总数。
   - AlgoOS 用 0.5 s 轮询目录，单提交端到端会额外引入 0–500 ms 检测延迟；这是平台开销的一部分，建议先暴露 `--poll-interval` 或增加 inotify 模式，并单独报告检测延迟。
4. **对照的规模要对齐。** 「AlgoOS 多节点」不能对「DOMjudge 单 judgehost」；应为 DOMjudge 配置相同数量的 judgehost，并把两边都固定到相同的 CPU 核上。
5. **预算策略：按量付费 + 快照。** 不要为整个月租一台 24 核机器；开发/调试用 8 vCPU 小实例，正式实验周临时开大规格，跑完导出数据、打快照、释放实例。

---

## 1. 下单前的三个决策

### 1.1 先测哪个版本

| 选项 | 适合时机 | 说明 |
| --- | --- | --- |
| 当前单机链路（`test_pipeline.py` + `eval_server`） | 现在就能做 | 作为「单机基线」，对比 DOMjudge 单节点；数据可直接写进中期报告和最终报告的基线章节 |
| 分布式主线（网关/队列/Worker） | 11 月下旬实现后 | 复用同一套题包、trace、指标和脚本；只更换 runner 适配器 |
| 两者都测 | 推荐 | 单机基线证明「单进程内核」的效率；分布式实验回答「加节点是否值得」 |

### 1.2 服务器拓扑怎么选

| 方案 | 适用条件 | 成本 | 风险 |
| --- | --- | --- | --- |
| **A. 多台云主机（推荐）** | 任意云厂商；每台就是一台 VM | 中 | 网络在 VPC 内，延迟 <1 ms；需要多台实例管理 |
| **B. 单台裸金属/支持嵌套虚拟化的服务器 + KVM** | 控制台明确支持嵌套虚拟化，或租裸金属 | 高 | 普通 ECS 通常无 `/dev/kvm`；宿主机争抢会影响所有 VM |
| **C. 单台云主机 + Docker 逻辑节点** | 预算最低、只做逻辑分布式验证 | 低 | 不是真正的 VM 隔离；报告中必须标注「逻辑节点，非独立内核」 |

**建议：** 正式实验用 A；如果学校/实验室已有物理机，用 B；C 只作为降级方案。

### 1.3 下单前检查清单

```bash
# 在目标服务器上执行
lscpu | grep -E 'vmx|svm'          # 有输出才可能支持 KVM
ls -l /dev/kvm                     # 不存在则不能跑 KVM
systemd-detect-virt                # 输出 kvm/vmware 等表示当前已是虚拟机
grep -c . /sys/fs/cgroup/cgroup.controllers   # cgroup v2
```

如果 `systemd-detect-virt` 显示当前是虚拟机且 `/dev/kvm` 不存在，**直接选方案 A**，不要尝试嵌套 KVM。

---

## 2. 推荐拓扑与规格

### 2.1 方案 A：多台云主机

最小正式实验拓扑（总 20 vCPU）：

| 角色 | 规格 | 运行内容 | 说明 |
| --- | ---: | --- | --- |
| `algoos-control` | 4 vCPU / 8 GiB / 100 GB ESSD | AlgoOS 控制面（Postgres、NATS、MinIO、Redis、网关、聚合器）；DOMjudge 的 MariaDB + domserver | 两种系统共用控制机，但**顺序运行**，不同时启动 |
| `algoos-eval-01` | 8 vCPU / 16 GiB / 100 GB ESSD | AlgoOS Eval Worker；DOMjudge judgehost × 6 | 评测主节点 |
| `algoos-eval-02` | 8 vCPU / 16 GiB / 100 GB ESSD | 同上 | 分布式扩展节点；单机对比阶段可不开 |
| `algoos-loadgen`（可选） | 2 vCPU / 4 GiB | 回放客户端、监控 | 也可复用 control，但注意其 CPU 干扰 |

- 只做单机对比时，`control` + `eval-01` 两台即可（12 vCPU）。
- 所有实例：**同可用区、同 VPC、同子网、同 CPU 机型**；不要混用突发性能型（t6/SA5 等），否则 CPU 积分会污染结果。
- 系统盘 ESSD，100 GB 足够；题目和输出很少，瓶颈在 CPU 不在磁盘。

### 2.2 方案 B：单台服务器 + KVM

| VM | vCPU / 内存 | 角色 |
| --- | ---: | --- |
| `algoos-control` | 2 / 6 GiB | 控制面 |
| `algoos-compile-01` | 2 / 4 GiB | 编译 Worker（分布式阶段） |
| `algoos-eval-01` | 4 / 6 GiB | 评测节点 1 |
| `algoos-eval-02` | 4 / 6 GiB | 评测节点 2 |
| `algoos-special-01` | 2 / 4 GiB | 特判（无特判负载时可不开） |

- 宿主机建议 **16 核 / 32 GiB 起步，24 核 / 64 GiB 更稳**；至少保留 4 个物理核给宿主机和 I/O。
- 所有 VM 使用同一 qcow2 基础镜像；实验前从快照恢复。
- vCPU 用 `--cpuset` 绑定到互不重叠的物理核；宿主机上关闭不必要的定时任务。

### 2.2.1 当前环境：WSL2 内嵌套 KVM（已落地）

本机为 Windows 11（Intel Core 5 220H，12 核 / 16 线程，32 GiB），WSL2 Ubuntu 已启用嵌套虚拟化：

- `/dev/kvm` 存在，`kvm_intel nested=Y`；
- QEMU 10.2.1、libvirt 12.0，`virt-host-validate` 的 KVM 检查全部 PASS；
- 当前 WSL 分配 16 vCPU / 15 GiB。

已按「控制 + 2 评测节点」落地 3 台 VM（脚本见 `deploy/experiment/wsl-kvm/`）：

| VM | vCPU | 内存 | IP | cpuset | 角色 |
| --- | ---: | ---: | --- | --- | --- |
| `algoos-control` | 4 | 4 GiB | 192.168.122.100 | 0-3 | DOMjudge MariaDB + domserver；AlgoOS 控制面；NFS 服务端 |
| `algoos-eval-01` | 4 | 4 GiB | 192.168.122.101 | 4-7 | judgehost / Eval Worker |
| `algoos-eval-02` | 4 | 4 GiB | 192.168.122.102 | 8-11 | judgehost / Eval Worker |

- 12–15 号 WSL vCPU 留给 WSL、Windows 和回放客户端，不与 VM 重叠。
- 3 台合计 12 vCPU / 12 GiB。当前已通过 `.wslconfig` 把 WSL 上限设为 20 GiB，并设置 `vmIdleTimeout=-1` 防止空闲关闭；若按 2.2 的 5 VM 完整拓扑，再提高到 24 GiB。
- control 通过 NFS 导出 `/srv/algoos`，两个 eval 节点自动挂载，AlgoOS 的 `submissions/`、`result/` 可放在该共享目录上。
- `deploy/experiment/domjudge/deploy-all.sh` 可把 DOMjudge 对照组部署进 VM：control 跑 MariaDB + domserver，两个 eval 各跑一个 judgehost。
- **这是二级嵌套虚拟化**（Windows → WSL2 → KVM）：只用于 AlgoOS 与 DOMjudge 的相对比较，绝对性能不能与裸金属/云主机数据混写。
- WSL 在空闲时可能自动关闭，导致 VM 停止。实验期间保持一个 WSL 终端打开；VM 已设置 `virsh autostart`，WSL 重新启动后会自动拉起。需要长时间无人值守时，可在 `C:\Users\<用户>\.wslconfig` 增加 `[wsl2]\nvmIdleTimeout=86400000` 后执行 `wsl --shutdown` 重启 WSL。
- 实验前停止 WSL 里已有的 Docker/DOMjudge（`cd ~/domjudge && docker compose down`），避免与 VM 争抢 CPU、内存。

### 2.3 方案 C：单台云主机 + Docker 逻辑节点

- 1 台 16 vCPU / 32 GiB 云主机。
- 用 Docker 的 `--cpuset-cpus`、`--memory` 和独立 network 模拟 3 个逻辑节点。
- 报告中明确写「逻辑节点共享同一内核，验证的是调度与协议，不是跨机隔离」；不能与 DOMjudge 的 KVM/多机结论混写。

### 2.4 成本控制

- 云厂商按量付费价格随地域/机型波动，下单前用控制台价格计算器确认；一般 8 vCPU 实例约几元/小时，24 核裸金属约十几到几十元/小时。
- 建议节奏：开发调试用 8 vCPU 小实例；正式实验集中 2–3 天开全拓扑；跑完立即导出 `artifacts/experiments/` 并释放实例。
- 每个角色先做基础镜像快照；实验前从快照克隆，避免环境污染。

---

## 3. 服务器与 VM 初始化

### 3.1 基础配置（所有节点）

- 镜像：**Ubuntu 24.04 LTS**（内核 ≥ 6.8，cgroup v2，systemd）。
- 创建普通用户 + SSH 密钥登录，禁用密码登录；只开放 SSH 和必要的 API 端口。
- 时间同步：`chrony`，所有节点同一 NTP 源；日志用 UTC。
- 监控工具：`sysstat`（sar/iostat/pidstat）、`jq`、`htop`、`docker stats`。
- 关闭自动更新，避免实验中途升级：

```bash
sudo apt update && sudo apt install -y chrony sysstat jq unzip htop \
  build-essential cmake git python3 python3-venv python3-pip libjson-c-dev
sudo systemctl enable --now chrony
sudo systemctl disable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
```

- 稳定性和文件描述符：

```bash
sudo tee /etc/sysctl.d/99-algoos-bench.conf >/dev/null <<'EOF'
vm.swappiness = 1
vm.overcommit_memory = 1
fs.file-max = 1048576
net.core.somaxconn = 4096
EOF
sudo sysctl --system
```

- 透明大页设为 `madvise`（减少内存分配抖动）：

```bash
echo madvise | sudo tee /sys/kernel/mm/transparent_hugepage/enabled
```

- CPU 调度器如果可写，设为 `performance`；云主机通常不允许，记录实际值即可：

```bash
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "not available"
```

### 3.2 Docker 与镜像加速

```bash
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

国内节点在 `/etc/docker/daemon.json` 配置可用镜像站，然后 `sudo systemctl restart docker`。镜像版本全部固定为 `domjudge/domserver:9.0.1`、`domjudge/judgehost:9.0.1`、`mariadb:11.4`，并记录 `docker image inspect` 的 digest。

### 3.3 方案 B 的 KVM 初始化

```bash
sudo apt install -y qemu-kvm libvirt-daemon-system libvirt-clients \
  virtinst cloud-image-utils libguestfs-tools
sudo systemctl enable --now libvirtd
```

创建私有网络 `algoos-net`（例如 `192.168.100.0/24`），固定每台 VM 的 MAC/IP；下载 Ubuntu cloud image 作为基础镜像：

```bash
mkdir -p ~/vm-images && cd ~/vm-images
wget https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
cp noble-server-cloudimg-amd64.img noble-base.qcow2
```

用 cloud-init seed 写入用户、SSH key、主机名和基础包，然后：

```bash
virt-install --import --name algoos-eval-01 \
  --memory 6144 --vcpus 4 --cpuset=4-7 \
  --disk path=~/vm-images/algoos-eval-01.qcow2,format=qcow2,bus=virtio \
  --network network=algoos-net,model=virtio \
  --os-variant ubuntu24.04 \
  --cloud-init user-data=~/vm-images/user-data.yaml,meta-data=~/vm-images/meta-data.yaml \
  --noautoconsole
```

- 用 `virsh vcpuinfo` 和 VM 内 `taskset -pc $$` 确认绑定生效。
- 每台 VM 实验前恢复快照：

```bash
virsh snapshot-create-as algoos-eval-01 base "clean base"
virsh snapshot-revert algoos-eval-01 base
```

在 WSL2 内，上述创建流程已封装为 `deploy/experiment/wsl-kvm/create-vms.sh`：自动下载 Ubuntu cloud image、为 DHCP 固定租约、生成 cloud-init seed，并安装 Docker、编译工具和 NFS。日常用 `start-vms.sh` 拉起网络和 VM。

### 3.4 环境清单（每次实验必存）

记录并写入 `artifacts/experiments/<run>/env.json`：

- 云厂商/地域/实例规格、vCPU/内存/磁盘
- 内核版本、发行版、cgroup 版本
- GCC/glibc 版本、AlgoOS git commit
- DOMjudge 版本与镜像 digest
- CPU 型号、超线程状态、频率策略、CPU steal
- 题目包/source 语料/trace 的 SHA-256

---

## 4. 部署 DOMjudge 对照组

### 4.1 组件拓扑

| 节点 | 容器 | 说明 |
| --- | --- | --- |
| `algoos-control` | `mariadb` + `domserver` | Web、REST API、数据库；不跑评测 |
| `algoos-eval-01` | `judgehost-0` … `judgehost-N-1` | 每个容器一个 judgedaemon，绑定一个 CPU 核 |
| `algoos-eval-02` | 同上 | 多节点扩展时加入 |

- 所有 judgehost 的 `DOMSERVER_BASEURL` 指向 control 的内网地址（如 `http://10.0.0.10/`）。
- 版本必须与 domserver 完全一致（9.0.1）。
- 每个 judgehost 容器保留 `privileged: true`、`cgroup: host`、`/sys/fs/cgroup` 挂载。
- `DAEMON_ID` 从 0 开始递增；DOMjudge 会把 judgedaemon 限制到对应 CPU 核，部署后必须用 `taskset`/日志确认。

多 judgehost 的 compose 片段：

```yaml
  judgehost-0:
    image: domjudge/judgehost:9.0.1
    privileged: true
    cgroup: host
    hostname: judgedaemon-0
    environment:
      DOMSERVER_BASEURL: http://<CONTROL_IP>/
      JUDGEDAEMON_USERNAME: judgehost
      JUDGEDAEMON_PASSWORD: ${JUDGEDAEMON_PASSWORD}
      DAEMON_ID: 0
    volumes: ["/sys/fs/cgroup:/sys/fs/cgroup:rw"]
    networks: [dj]
  judgehost-1:
    image: domjudge/judgehost:9.0.1
    privileged: true
    cgroup: host
    hostname: judgedaemon-1
    environment:
      DOMSERVER_BASEURL: http://<CONTROL_IP>/
      JUDGEDAEMON_USERNAME: judgehost
      JUDGEDAEMON_PASSWORD: ${JUDGEDAEMON_PASSWORD}
      DAEMON_ID: 1
    volumes: ["/sys/fs/cgroup:/sys/fs/cgroup:rw"]
    networks: [dj]
```

### 4.2 关键配置对齐（对比前必须做）

| 项 | DOMjudge 默认 | AlgoOS 当前 | 对齐做法 |
| --- | --- | --- | --- |
| C++ 编译命令 | `g++ -x c++ -Wall -O2 -static -pipe` | `g++ -std=c++17 -O2 -pipe` | 在 DOMjudge `Languages → C++` 中把编译命令改成与 AlgoOS 完全一致（建议都去掉 `-Wall -static`，保留 `-std=c++17 -O2 -pipe`）；或给 AlgoOS 加 `-static -Wall`，二选一，写进实验清单 |
| GCC 版本 | judgehost 镜像自带工具链 | 宿主机 g++ | 记录两边 `g++ --version`；差异大时重建 judgehost 镜像或统一容器内工具链 |
| 时限 | CPU 时间为主，另有 wall 超时 | `RLIMIT_CPU` 秒级向上取整 + wall=CPU | 统一题目的 `time_limit`；TLE 实验单独设计，避免用 sleep 类程序混淆 CPU/wall |
| 内存 | cgroup `memory.max` / RSS | `RLIMIT_AS`（地址空间） | 内存题按「明显超出限制」设计；报告里说明两种语义不同，不能宣称 MLE 完全等价 |
| 比较规则 | 可配置 output validator | `common`：统一换行、去行末空白、去末尾空行 | 为 DOMjudge 题包写与 AlgoOS `common` 完全一致的自定义 validator；所有题共用同一份 |
| 语言 | 可启用多语言 | 仅 C++ | 只启用 C++，关闭其他语言 |
| 判题模式 | 全部测试点 | 当前全部测试点 | AlgoOS 实验时使用 `all_cases`（不短路），与 DOMjudge 对齐；短路机制另做实验 |

### 4.3 健康检查

```bash
cd ~/domjudge
docker compose ps
docker compose logs --tail=50 judgehost-0
curl -I http://<CONTROL_IP>/
```

- Judgehost 日志出现 `Judge started on judgedaemon-...` 和 `No submissions in queue` 即注册成功。
- 先提交 1 个 AC、1 个 WA，确认 verdict 与 AlgoOS 基线一致，再开始正式实验。

---

## 5. 部署 AlgoOS

### 5.1 当前单机链路

在评测节点上：

```bash
git clone <repo-url> AlgoOS
cd AlgoOS
make -C judge/native

# 单机基线：worker 数按实验配置
python3 test_pipeline.py --workers 4
```

建议用 systemd 托管并绑定 CPU：

```ini
[Unit]
Description=AlgoOS pipeline
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/<user>/AlgoOS
ExecStart=/usr/bin/python3 test_pipeline.py --workers 4
CPUAffinity=0-5
Restart=no
Delegate=yes

[Install]
WantedBy=multi-user.target
```

- 评测节点上保留 0–5 号核给评测，6–7 号核给监控/负载生成器；两边（DOMjudge 和 AlgoOS）使用同一组核。
- 分布式版本按 [distributed_implementation_plan.md](distributed_implementation_plan.md) 第 7 节部署；控制面在 `control`，Worker 在评测节点，Worker 的 systemd 单元启用 `Delegate=yes` 以便 cgroup 委派。

### 5.2 负载生成器放哪里

- 推荐放在**独立的小节点**（`loadgen`），通过 HTTP 提交 DOMjudge、通过共享目录提交 AlgoOS。
- 如果只有一台评测节点，可把生成器放在评测节点 6–7 号核上；必须记录生成器 CPU 占用，证明它 < 2% 总 CPU，否则结论要打折扣。
- 两种系统的客户端最好由同一台机器、同一个脚本、同一个时钟运行；AlgoOS 的目录通过 NFS/SSHFS 暴露给 loadgen，或直接在评测节点本机运行并固定核。
- 当前 WSL2-KVM 拓扑下，回放器放在 WSL 宿主机（绑定 12–15 号核）或 control VM；AlgoOS 的提交通过 control 导出的 NFS 目录写入，DOMjudge 通过 REST API 提交。

### 5.3 回放工具接口

```text
experiments/runners/replay.py
  --system algoos|domjudge
  --trace traces/<name>.jsonl
  --out raw/<run_id>.csv
  --concurrency <n>
```

每条 trace 记录：

```json
{"seq": 1, "arrival_ms": 0, "problem_id": "p_sieve", "source": "sources/p_sieve/ac_01.cpp", "expected": "AC"}
```

- `arrival_ms` 是相对实验开始的毫秒偏移；生成器按绝对单调时钟调度，不能用串行 `sleep` 累加。
- 记录 `submit_ts`、`accept_ts`、`final_ts`、`verdict`、`retry`，以 `time.monotonic_ns()` 为准。

---

## 6. 题包与提交语料

### 6.1 题目集建议

至少 8 题，覆盖不同瓶颈（题量可按时间裁剪）：

| 题号 | 负载特征 | 测试点数 | 建议时限 | 目的 |
| --- | --- | ---: | ---: | --- |
| P1 `hello` | 极短、I/O | 1 | 1000 ms | 测量平台固定开销 |
| P2 `sum` | 中等 I/O | 10 | 1000 ms | 常规吞吐 |
| P3 `sieve` | CPU 200–800 ms | 10 | 1000 ms | CPU 密集 |
| P4 `matmul` | CPU 接近时限 | 5 | 1000 ms | TLE 边界 |
| P5 `memory` | 100–300 MiB | 5 | 1000 ms | 内存限制 |
| P6 `manycases` | 20 个测试点 | 20 | 500 ms | 调度/聚合开销 |
| P7 `bigout` | 大输出 | 5 | 1000 ms | 输出/IO |
| P8 `special` | 浮点特判（可选） | 5 | 1000 ms | validator 路径（DOMjudge 支持，AlgoOS 当前返回 SE，暂不比较） |

### 6.2 两套题包必须同源

为每题同时生成：

```text
problems/<id>/problem.json          # AlgoOS
problems/<id>/tests/*.in/*.out      # 共用测试数据

domjudge-packages/<id>/problem.yaml
domjudge-packages/<id>/data/sample/*.in/*.ans
domjudge-packages/<id>/data/secret/*.in/*.ans
domjudge-packages/<id>/output_validators/common/validate.sh   # 与 AlgoOS common 规则一致
```

- 测试数据只维护一份，脚本生成两边格式；两边测试点的输入/答案 SHA-256 必须一致。
- 时限映射：DOMjudge `problem.yaml` 的 `time_limit` 单位为秒，AlgoOS `time_limit_ms` 为毫秒；内存同理（MiB）。
- DOMjudge 默认比较器可能与 AlgoOS 的 trim 规则不同；务必用自定义 validator 统一，否则 WA 数量会误导吞吐。

### 6.3 提交语料

每种题目准备 AC/WA/TLE/RE/CE 五类源码；按下列比例组成基础语料：

| 类别 | 比例 | 说明 |
| --- | ---: | --- |
| AC | 60% | 正常完成 |
| WA | 15% | 输出错误，快速结束 |
| TLE | 10% | 接近或超过时限 |
| RE | 5% | 崩溃/非零退出 |
| CE | 5% | 编译失败 |
| MLE | 5% | 明显超内存 |

- 每份源码分配唯一 `submission_id`，并保存 `expected_verdict`；OS 级正确性以该 oracle 为准。
- 规模：E0 用 150–200 份；吞吐实验用 300–500 份；burst 实验用 10/50/100/500。
- 语料和 trace 生成后计算哈希，写入环境清单。

---

## 7. 指标与埋点

### 7.1 指标定义

| 指标 | 定义 | 采集点 |
| --- | --- | --- |
| 端到端周转 | `final_ts - submit_ts` | 外部回放客户端 |
| 排队等待 | 评测开始 − 提交（扣除已知检测/网络） | 系统内部时间戳 |
| 编译耗时 | 编译开始 → 编译结束 | AlgoOS metrics；DOMjudge `judging`/日志 |
| 单测试点运行耗时 | `cpu_ms` / wall | AlgoOS `eval_server`；DOMjudge `judging_run` |
| 吞吐 | 完成提交数 / 实验窗口 | 客户端 |
| 尾延迟 | p95 / p99 / max | 客户端 |
| 资源利用率 | CPU busy / 总核数；内存峰值 | `sar`、`pidstat`、`docker stats` |
| 每提交 CPU 时间 | 所有测试点 CPU 之和 | 系统内部 |
| verdict 一致率 | 与 oracle 一致的比例 | 客户端 + DB |
| 失败率 | 超时/系统错误/重试比例 | 客户端 + 系统日志 |

### 7.2 AlgoOS 埋点

`eval_server` 已经返回每个测试点的 `cpu_ms`、`mem_kb`、`out_bytes`，但 `pipeline.py` 目前只把提交级 `elapsed_ms` 写进 `result.tsv`。实验前建议增加一个**只增不改**的 `result/metrics.jsonl`：

```json
{"submission_id":"0001","problem_id":"sieve","source":"...","file_mtime_ns":0,
 "detected_ns":0,"compile_start_ns":0,"compile_end_ns":0,
 "eval_start_ns":0,"eval_end_ns":0,"finished_ns":0,
 "verdict":"AC","tests":[{"id":"1","verdict":"AC","cpu_ms":120,"mem_kb":15420,"out_bytes":1024}]}
```

- 保留现有 `result.tsv` 表结构，避免破坏既有约定。
- 若时间允许，增加 `--poll-interval` 参数或 inotify 模式，单独测「检测延迟」；否则在报告中明确：AlgoOS 端到端延迟包含 0–500 ms 轮询。
- 对 `eval_server` 的改动仅限输出字段，不改判定逻辑。

### 7.3 DOMjudge 埋点

- 端到端：回放客户端记录 POST 时间和轮询到 `judgement_type_id` 的时间。
- 纯评测：数据库 `judging.starttime/endtime`、`judging_run.runtime`；必要时用 API `/judgements`。
- 不要用容器日志时间戳做统计，日志不是稳定事实来源。

### 7.4 系统监控

每个实验同时启动：

```bash
sar -u 1 3600 > artifacts/experiments/<run>/cpu.log &
sar -r 1 3600 > artifacts/experiments/<run>/mem.log &
pidstat -u -r -p ALL 1 3600 > artifacts/experiments/<run>/pid.log &
```

- 记录 CPU steal（`sar -u` 的 `%steal`）；steal 超过阈值（如 5%）的轮次标记为无效。
- 记录云主机 CPU 频率/温度不可见时，至少记录 `%steal` 和实际耗时波动。

---

## 8. 实验矩阵

> 重复次数：预实验 3 次，正式实验 5–15 次；至少报告中位数和 p95/p99。每个配置独立冷/热缓存两组。

| 编号 | 目的 | 系统/配置 | 负载 | 重复 | 主要输出 |
| --- | --- | --- | --- | ---: | --- |
| E0 | verdict 一致性 | AlgoOS vs DOMjudge，单节点 | 150–200 份全类别语料 | 1（稳定后可 3） | 混淆矩阵、不一致清单 |
| E1 | 单提交延迟 | 两边各 1 个执行槽 | 每类 30 份，冷/热 | 5 | p50/p95/p99、分阶段耗时 |
| E2 | 并发扩展 | workers/judgehosts = 1/2/4/6 | 300 份 Poisson，λ=5/s | 5 | 吞吐、尾延迟、CPU 利用率 |
| E3 | 突发排空 | 两边同槽位数 | 10/50/100/500 同时到达 | 5 | 排空时间、p99、最大等待 |
| E4 | 混合负载 | 60/15/10/5/5/5 比例 | 500 份 | 5 | 总吞吐、分类延迟、公平性 |
| E5 | 开销分解 | 两边 | 100 份混合 | 3 | 检测/排队/编译/运行/聚合占比 |
| E6 | 稳定性 | 两边同配置 | 1000 份、持续 1 h | 3 | 失败率、内存增长、漂移 |
| E7 | 节点扩展（AlgoOS） | 1/2/3 个 eval 节点 | 500 份 | 5 | 吞吐、跨节点开销 |
| E8 | 调度策略（AlgoOS） | FCFS/SJF/Aging/最早完成/缓存亲和 | 固定 trace | 5 | 平均等待、p95、饥饿次数 |
| E9 | 缓存局部性（AlgoOS） | 冷/热缓存、同题/交替题 | 500 份 | 5 | 命中率、传输量、周转 |
| E10 | 故障注入（AlgoOS） | kill worker/special 节点 | 200 份 | 3 | 恢复时间、重复率、缺失率 |

- E7–E10 只对 AlgoOS 内部策略做比较，不需要 DOMjudge 对照；DOMjudge 的扩展对照就是 E2（增加 judgehost）。
- E0 必须在 E1–E6 之前通过；verdict 不一致时先修判定/validator，不要继续做性能结论。

### 8.1 最小可行实验（时间/预算不足时）

只租 1 台 8 vCPU / 16 GiB：

1. E0（150 份）验证 verdict 一致；
2. E1（每类 30 份）测单提交；
3. E2（槽位 1/4/6，300 份，5 次）测扩展；
4. E3（10/50/100）测突发。

这四组足以支撑「单机基线 vs DOMjudge」的报告章节；分布式 E7–E10 留到第二阶段。

---

## 9. 运行协议

### 9.1 每轮实验前

1. 恢复 VM 快照 / 重启容器，确认无残留提交和缓存。
2. 冷缓存组：`sync; echo 3 | sudo tee /proc/sys/vm/drop_caches`；停止并重建数据库容器（或清空实验数据）。
3. 热缓存组：先跑 20 份预热提交，不计入统计。
4. 启动 `sar/pidstat` 监控。
5. 记录 `env.json` 和本轮配置。

### 9.2 运行顺序

- **同一时间只运行一个系统**；另一个系统的容器/服务全部停止。
- 采用 A-B-A 交替：AlgoOS → DOMjudge → AlgoOS，抵消云主机随时间的漂移；每轮重新预热。
- 同一配置的所有重复轮次使用相同 trace 和相同 source 语料。
- 正式实验期间不在服务器上做编译、更新、备份等重负载操作。

### 9.3 每轮实验后

1. 等待队列排空（所有提交到达终态）；记录实际 wall time。
2. 停止监控；导出客户端 CSV、系统内部指标、容器日志。
3. 检查失败/重试/超时；超过阈值（如 1%）的轮次标记无效并重跑。
4. 归档到 `artifacts/experiments/<date>_<experiment>/<run_id>/`，结构与计划文档第 11 节一致。
5. 清理提交数据和临时输出，避免磁盘和缓存影响下一轮。

### 9.4 数据目录

```text
artifacts/experiments/20261125_algoos_vs_domjudge/
├── env.json                 # 硬件/软件/镜像/commit
├── trace.jsonl              # 本轮到达序列
├── sources.sha256           # 语料哈希
├── raw/
│   ├── algoos_e1_r1.csv
│   └── domjudge_e1_r1.csv
├── logs/
└── summary.md               # 结论与图表索引
```

---

## 10. 统计、图表与结论边界

### 10.1 统计

- 每轮先算分位数：p50/p95/p99、均值、标准差、最大值。
- 多轮结果报告**中位数 [IQR]**，并给 95% bootstrap 置信区间。
- 两组比较用 Mann-Whitney U（不假设正态）；同时报告效应量（比值或 Cliff's delta），不要只报 p 值。
- 预先固定随机种子、trace 和重复次数；不要根据结果挑最好的一轮。

### 10.2 必备图表

1. 延迟 CDF（AlgoOS vs DOMjudge，单槽位/多槽位各一张）。
2. 吞吐随并发/槽位数变化曲线。
3. 突发排空曲线（完成数 vs 时间）。
4. 分阶段耗时堆叠图（检测/排队/编译/运行/聚合）。
5. verdict 混淆矩阵。
6. CPU 利用率与每提交 CPU 时间对比。
7. AlgoOS 节点扩展曲线（1/2/3 节点）。
8. 调度策略等待时间对比（E8）。

### 10.3 结论边界

- 只对已控制的硬件、题包、编译器、限额、版本和工作负载下结论。
- DOMjudge 是成熟基线，不宣称「全面超越」；分别说明「判定正确性」「单提交延迟」「吞吐」「尾延迟」「资源效率」哪些指标有差异、差异多大。
- 明确区分「实测」「模拟」「未验证」；cgroup/namespace/seccomp 等隔离能力必须在评测 VM 上逐项验证后才能写「已验证」。
- 容器化差异、chroot 差异、轮询检测、数据库/API 开销都要在局限性章节说明。

---

## 11. 排期与预算

| 时间 | 任务 | 产物 |
| --- | --- | --- |
| 11/10–11/16 | WSL2-KVM 3 节点已就绪；同源题包生成；AlgoOS 指标埋点；单机 E0/E1 预实验 | 3 台 VM、同源题包、`metrics.jsonl`、预实验数据 |
| 11/17–11/23 | 分布式主线联调；E2/E3/E4 试点 | 跨节点结果、试点 CSV |
| 11/24–12/06 | 正式实验 E0–E10；重复 5–15 次；补齐图表 | 原始 CSV、图表、总结 |
| 12/07–12/13 | 报告实验章节；复核结论边界；归档 | 报告初稿、`artifacts/experiments/` |
| 12/14–12/20 | 最终验收与修复 | 可重跑脚本与证据 |

预算建议：

- 当前方案在 WSL2 内跑 KVM，零租用成本；代价是二级嵌套虚拟化，绝对性能不可与裸金属混写。
- 如果正式实验需要更多节点或更稳定的尾延迟，再按方案 A 临时租用云主机（control 4 vCPU + eval 8 vCPU ×2），按量运行 2–3 天。
- 若使用单台裸金属 + KVM：24 核 / 64 GiB，按天计费，实验前后打快照。
- 以云厂商控制台实时价格为准；先用 WSL 方案把小规模流程跑通，再决定是否租大规格。

---

## 12. 风险与降级

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 云主机不支持嵌套虚拟化 | KVM 方案无法启动 | 改方案 A（多台云主机）或 C（Docker 逻辑节点），报告中标注 |
| DOMjudge 与 AlgoOS 编译器/参数不一致 | 结论不可比 | 统一编译脚本和 GCC 版本；差异无法消除时在报告中列为混杂因素 |
| 沙箱/chroot 差异 | 运行耗时不同 | 同时报告端到端和纯评测；不把差异全部归因于架构 |
| 云主机 CPU steal/积分 | 延迟抖动 | 不用突发型实例；记录 `%steal`；A-B-A 交替；异常轮次剔除 |
| AlgoOS 轮询延迟 | 单提交延迟被高估 | 增加 `--poll-interval`/inotify 或单独报告检测延迟 |
| 题包/validator 不一致 | verdict 偏差 | E0 先行；两套题包由同一份数据生成；validator 共用 |
| 实验规模过大 | 时间/预算超支 | 先做最小可行实验 E0–E3；E7–E10 作为第二阶段 |
| DOMjudge 版本/镜像漂移 | 不可复现 | 固定 9.0.1 与 digest；实验前 `docker compose pull` 后记录 digest |
| 单台宿主机争抢 | VM 之间互相影响 | CPU pinning、顺序运行、快照恢复、记录宿主机负载 |

---

## 13. 交付清单

1. `env.json`：每轮实验的硬件、系统、编译器、镜像、commit、题包/trace 哈希。
2. 同源题包生成脚本与两套题包（AlgoOS + DOMjudge）。
3. 提交语料与 `trace.jsonl`，含 oracle verdict。
4. 回放客户端与 AlgoOS 指标埋点（`metrics.jsonl`）。
5. E0–E6（必做）和 E7–E10（分布式阶段）原始 CSV。
6. 统计脚本与图表：CDF、吞吐曲线、排空曲线、分阶段耗时、混淆矩阵、资源利用率。
7. 一页「环境与配置清单」和「公平性说明」，供报告直接引用。
8. 结论边界：哪些是实测、哪些是模拟、哪些未验证。

---

## 相关文档

- [分布式实施方案（部署拓扑与实验设计）](distributed_implementation_plan.md)
- [完整 UML 与接口规范](next_step.md)
- [AlgoOS 部署与使用](../ops/deployment.md)
- [DOMjudge 部署与使用](../ops/domjudge.md)
- [中期汇报](../../midterm_report.md)
- [项目排期](plan_revised.md)
