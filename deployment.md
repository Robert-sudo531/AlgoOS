# AlgoOS 部署与自测指南

本文按“先本机、再多机、最后 VM”的顺序给出可执行步骤。第一次测试建议先完成
[本机单机多进程](#1-本机单机多进程推荐先做)；它不需要改主机名、systemd 或防火墙。

> **验证边界**：本机模式会启动真实 PostgreSQL、Redis、NATS、MinIO 容器和原生 C++
> Worker，但默认关闭 cgroup/namespace/seccomp。它不是生产隔离验证。KVM 参考多节点与
> 跨机实验目前只有部署资产，未在本仓库环境中执行。

## 0. 选择部署方式

| 方式 | 适用场景 | 是否需要 Docker | 当前验证状态 |
|---|---|---|---|
| 本机多进程 | 开发、演示、集成测试 | 是 | 已实测 |
| systemd 多机 | 真实多节点试部署 | 控制面/基础设施可用 Docker，Worker 节点不需要 | 资产已备，未在本机执行多机 |
| KVM 参考多节点 | 课程/生产拓扑演练 | 取决于基础设施 | 未执行；见 [deploy/kvm/README.md](deploy/kvm/README.md) |

---

## 1. 本机单机多进程（推荐先做）

### 1.1 前置条件

需要 Linux x86_64、Docker、g++ 16+、CMake/Ninja、Python 3.13+、`uv`。

```bash
uname -m
g++ --version
docker version
uv --version
```

Docker 未启动时：

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# 重新登录后生效
```

构建工具可选：

```bash
# Arch 示例；其他发行版用对应包管理器
sudo pacman -S --needed base-devel cmake ninja python uv docker
```

### 1.2 准备配置

```bash
cd /path/to/AlgoOS

# 首次部署；已有 .env 时不要覆盖
cp -n deploy/compose/.env.example deploy/compose/.env

# 查看镜像与凭据
sed -n '1,20p' deploy/compose/.env
```

默认使用：

- PostgreSQL：`algoos` / `algoos`，库 `algoos`
- MinIO：`algoos` / `algoos-secret`
- MinIO 镜像：`cgr.dev/chainguard/minio@sha256:9158c67a...`

如果修改了 `.env` 里的用户名/密码/端口，需要同时导出对应的 `ALGOOS_DB_DSN`、
`ALGOOS_S3_ACCESS_KEY`、`ALGOOS_S3_SECRET_KEY`、`ALGOOS_REDIS_URL` 等变量再运行
`algoos` 命令；否则 CLI/测试会使用默认的本机地址。

当前环境 Docker Hub 的 `minio/minio` 镜像不可用，使用上述 Chainguard 构建的
AGPLv3 社区版；它不是 `minio/minio` 上游官方镜像，也不是生产 MinIO 集群验证。
如果镜像源不可达，可改 `POSTGRES_IMAGE`、`REDIS_IMAGE`、`NATS_IMAGE`、`MINIO_IMAGE`；
MinIO 完全不可用时，临时调试可：

```bash
export ALGOOS_S3_MODE=moto   # 只做 S3 兼容测试，不能写成 MinIO 验证
```

### 1.3 安装 Python 依赖并构建 C++

```bash
uv sync
export PATH="$PWD/.venv/bin:$PATH"

cmake -S cpp -B build/cpp -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/cpp --parallel "$(nproc --ignore=4)"

./build/cpp/algoos-core-tests
```

### 1.4 启动集群

```bash
deploy/scripts/local_cluster.sh start
```

脚本会：

1. `docker compose up -d postgres redis nats minio`；
2. 启动 `algoos-compile/eval/special` 原生 Worker；
3. 启动 compile/eval/special 的 Python Agent；
4. 启动控制面和 API；
5. 在 `http://127.0.0.1:8080/` 提供 Web 页面。

默认每种 Worker 启动 2 个节点（`compile-01/02`、`eval-01/02`、`special-01/02`）。
数量可调，不是固定拓扑：

```bash
ALGOOS_COMPILE_NODES=3 ALGOOS_EVAL_NODES=4 ALGOOS_SPECIAL_NODES=2 \
  deploy/scripts/local_cluster.sh start
```

首次启动需要拉取镜像，可能较慢。查看状态和日志：

```bash
deploy/scripts/local_cluster.sh status
deploy/scripts/local_cluster.sh logs
```

### 1.5 健康检查

```bash
uv run algoos doctor
deploy/scripts/check_health.sh
uv run algoos nodes
```

预期：

- `doctor` 四项全部通过：PostgreSQL、object-store、NATS、Redis；
- `nodes` 至少看到默认的 2 个 compile、2 个 eval、2 个 special，状态 `ALIVE`；节点数量按上面的环境变量变化；
- API `http://127.0.0.1:8080/healthz` 返回 200。

### 1.6 导入题目并提交

```bash
uv run algoos import-problems
uv run algoos problems
```

提交 AC 样例：

```bash
uv run algoos submit \
  --problem a_plus_b \
  --file examples/a_plus_b.cpp
```

输出中有 `file_id`。查询直到终态：

```bash
FID=<上一步的 file_id>
while true; do
  out=$(uv run algoos status "$FID")
  echo "$out" | grep -E '"status": "(FINAL|CE|CANCELLED)"' >/dev/null && break
  sleep 1
done
echo "$out"
```

预期 `status=FINAL`、`verdict=AC`，测试点由存活的 eval 节点处理（默认两台时通常落在 `eval-01`/`eval-02`）。

### 1.7 自测主要判定

题目都用 `a_plus_b`，替换 `--file` 即可：

| 样例 | 预期 |
|---|---|
| `examples/a_plus_b.cpp` | `FINAL AC` |
| `examples/wrong_answer.cpp` | `FINAL WA` |
| `examples/compile_error.cpp` | `CE` |
| `examples/runtime_error.cpp` | `FINAL RE` |
| `examples/time_limit.cpp` | `FINAL TLE` |
| `examples/crash_signal.cpp` | `FINAL RE`（崩溃信号） |
| `examples/output_limit.cpp` | `WR`（输出超限） |
| `examples/memory_limit.cpp` | `MLE` 或 `RE`（取决于本机是否启用 cgroup） |
| `examples/special_checker_timeout.cpp` | `SE`（配合 `special_pair`，checker 超时） |

`WR` 表示输出超限（`reason=OUTPUT_LIMIT`），`WA` 表示内容不匹配（`ANSWER_MISMATCH`）；两者不再合并。

批量自测：

```bash
for f in a_plus_b wrong_answer compile_error runtime_error time_limit; do
  echo "=== $f"
  uv run algoos submit --problem a_plus_b --file "examples/$f.cpp" \
    | tee "/tmp/algoos-$f.json"
done
```

逐个取 `file_id` 后按 1.6 的方式轮询。

特判题：

```bash
uv run algoos submit --problem special_pair --file examples/special_correct.cpp  # AC
uv run algoos submit --problem special_pair --file examples/special_wrong.cpp    # WA
uv run algoos submit --problem special_pair --file examples/special_checker_error.cpp  # SE
```

特判链路是：评测机上传 `.out` → 特判机下载 `.out`/answer/checker → 在沙箱中运行出题者
checker → 按 exit 0/1/其他返回 AC/WA/SE。

用户取消：

```bash
FID=$(uv run algoos submit --problem a_plus_b --file examples/sleepy.cpp \
  | uv run python -c 'import json,sys; print(json.load(sys.stdin)["file_id"])')
uv run algoos cancel "$FID"
```

### 1.8 Web 页面

浏览器打开：

```text
http://127.0.0.1:8080/
```

本地默认不设置 `ALGOOS_API_TOKEN`，API 不要求 Bearer。若设置了 token，CLI 需使用同一个
`ALGOOS_API_TOKEN`，浏览器页面则需要在页面提供的 token 入口填入（以当前静态页面实现为准）。

### 1.9 重启 / 停止 / 清理

```bash
# 停止进程，保留容器卷
deploy/scripts/local_cluster.sh stop

# 再次启动，数据保留
deploy/scripts/local_cluster.sh start

# 停止并删除容器卷（不可恢复）
deploy/scripts/local_cluster.sh clean

# 交互式彻底重置本机运行状态
deploy/scripts/reset_local_cluster.sh
```

---

## 2. systemd 多机部署（进阶）

本节是给真实多机试部署的步骤，不是已经完成的多机验收。多台机器建议：

| 主机名 | 角色 | 进程 |
|---|---|---|
| `algoos-control` | 控制/API | `algoos-api`、`algoos-control` |
| `algoos-compile-01` | 编译池 | `algoos-compile-worker@compile-01` + agent |
| `algoos-compile-02` | 编译池 | `algoos-compile-worker@compile-02` + agent |
| `algoos-eval-01` | 评测池 | `algoos-eval-worker@eval-01` + agent |
| `algoos-eval-02` | 评测池 | `algoos-eval-worker@eval-02` + agent |
| `algoos-special-01` | 特判池 | `algoos-special-worker@special-01` + agent |
| `algoos-special-02` | 特判池 | `algoos-special-worker@special-02` + agent |

三种角色都可以继续加节点；这个 7 节点表只是参考拓扑，不是上限。评测调度按心跳表
发现 `eval-NN`，编译/特判通过共享 durable 在池内分配任务。

### 2.1 基础设施

PostgreSQL、Redis、NATS、MinIO 可以放在控制节点，也可以用外部服务。若用本仓库
Compose，注意它默认只监听 `127.0.0.1`：

```yaml
# deploy/compose/docker-compose.yml（多机时按需修改绑定地址）
ports:
  - "0.0.0.0:5432:5432"   # 仅限受信内网，并配置防火墙
```

需要放通的端口：

- PostgreSQL 5432、Redis 6379、NATS 4222、MinIO 9000、API 8080；
- 只允许集群内网访问，不要把 5432/6379/4222/9000 暴露到公网；
- NATS 当前未配置 TLS/认证，生产必须加反向代理/网络隔离。

MinIO 仍使用本文 1.2 的固定镜像/凭据，或在外部对象存储中创建同名 bucket 和凭据。

### 2.2 每台节点准备

所有节点安装 Docker（仅控制/基础设施需要）、g++、CMake/Ninja、Python、`uv`，然后：

```bash
sudo useradd --system --home /opt/algoos --shell /usr/sbin/nologin algoos || true
sudo mkdir -p /opt/algoos
sudo chown root:algoos /opt/algoos

# 将仓库放到 /opt/algoos，或 clone 到该路径
cd /opt/algoos
sudo chown -R algoos:algoos /opt/algoos
sudo -u algoos uv sync
sudo -u algoos env PATH="/opt/algoos/.venv/bin:$PATH" \
  cmake -S cpp -B build/cpp -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
sudo -u algoos env PATH="/opt/algoos/.venv/bin:$PATH" \
  cmake --build build/cpp --parallel 4
```

如果评测/特判节点要启用完整 cgroup/namespace/seccomp，需要 root 权限和 cgroup v2；
`algoos-eval-worker@.service` 已按 root 运行。

`algoos-eval-agent@.service` 和 `algoos-special-agent@.service` 也按 root 运行：原生 Worker
创建的 Unix Socket 权限是 `0600`，Agent 必须和 Worker 同 UID 才能连接。不要把这两个
Agent 改成非 root，除非同时修改 Native Worker 的 socket 权限模型。

### 2.3 配置环境变量

在控制节点生成 `/etc/algoos/algoos.env`：

```bash
sudo install -d /etc/algoos /etc/algoos/nodes /var/lib/algoos /var/cache/algoos
sudo cp deploy/env.example /etc/algoos/algoos.env
sudo chown root:algoos /etc/algoos/algoos.env
sudo chmod 0640 /etc/algoos/algoos.env
sudoedit /etc/algoos/algoos.env
```

至少修改：

```dotenv
ALGOOS_DB_DSN=postgresql://algoos:CHANGE_ME@<控制节点IP>:5432/algoos
ALGOOS_NATS_URL=nats://<控制节点IP>:4222
ALGOOS_S3_ENDPOINT=http://<控制节点IP>:9000
ALGOOS_S3_ACCESS_KEY=...
ALGOOS_S3_SECRET_KEY=...
ALGOOS_REDIS_URL=redis://<控制节点IP>:6379/0
ALGOOS_API_TOKEN=<长随机串>
ALGOOS_API_HOST=0.0.0.0
```

把同一个文件分发到所有节点（或各自填写相同的基础设施地址）。控制节点执行题目导入：

```bash
sudo -u algoos bash -c '
  cd /opt/algoos
  set -a; source /etc/algoos/algoos.env; set +a
  .venv/bin/algoos import-problems
'
```

不要把密码写进仓库；环境文件权限保持 `0640 root:algoos`。

### 2.4 安装并启动节点

`deploy/scripts/install_node.sh` 按主机名识别角色，支持编号节点：

```text
algoos-control
algoos-compile-01   algoos-compile-02   ...
algoos-eval-01      algoos-eval-02      ...
algoos-special-01   algoos-special-02   ...
```

在每台节点以 root 执行：

```bash
sudo ALGOOS_ROOT=/opt/algoos bash deploy/scripts/install_node.sh
```

它会：

- 安装 `deploy/systemd/*.service`；
- 生成 `/etc/algoos/nodes/<node>.json`；
- `systemctl enable --now` 对应 Worker/Agent（控制节点启用 API/Control）。

检查：

```bash
systemctl --type=service --state=running | grep algoos
sudo journalctl -u 'algoos-*' -n 100 --no-pager
```

### 2.5 多机验收

在控制节点：

```bash
ALGOOS_API_URL=http://127.0.0.1:8080 deploy/scripts/check_health.sh
uv run algoos nodes
```

从任意能访问 API 的机器：

```bash
export ALGOOS_API_URL=http://<控制节点IP>:8080
export ALGOOS_API_TOKEN=<与 env 一致>
uv run algoos submit --problem a_plus_b --file examples/a_plus_b.cpp
```

如果节点显示 `ALIVE` 但提交一直排队，依次检查：

1. Worker 的 Unix Socket 是否存在（`/run/algoos/<node>.sock`）；
2. NATS subject 是否有消费者（`algoos.compile.task`、`algoos.special.task`、
   `algoos.eval.task.eval-NN` 等）；
3. 各节点时间是否同步；
4. S3/DB/Redis 地址是否从该节点可达；
5. cgroup 权限是否阻止了 Worker 启动。

---

## 3. KVM / cloud-init 多节点参考

相关资产：

- [deploy/kvm/README.md](deploy/kvm/README.md)
- [deploy/kvm/provision.sh](deploy/kvm/provision.sh)
- [deploy/cloud-init/user-data.yaml](deploy/cloud-init/user-data.yaml)
- [deploy/inventory/nodes.example.yaml](deploy/inventory/nodes.example.yaml)

简要流程：

```bash
export ALGOOS_REPO_URL=...
export ALGOOS_SSH_PUBLIC_KEY=...
export ALGOOS_DB_DSN=...
export ALGOOS_NATS_URL=...
export ALGOOS_S3_ENDPOINT=...
export ALGOOS_REDIS_URL=...
export ALGOOS_API_TOKEN=...
bash deploy/kvm/provision.sh
```

要求宿主机已安装并启用 libvirt/KVM、能访问 Ubuntu cloud image。示例拓扑包含
1 个 control、2 个 compile、2 个 eval、2 个 special；节点数量不是协议上限，可按
`deploy/kvm/provision.sh` 的 hosts/macs 数组和 network.xml 扩展。**本仓库没有在真实
KVM 上执行过该脚本**；请把它当作部署模板，并按 [集群部署](docs/operations/cluster_deployment.md)
核对每个节点。

---

## 4. 故障排查

| 现象 | 检查 |
|---|---|
| `docker compose` 拉取失败 | 改 `deploy/compose/.env` 的 `*_IMAGE` 为可达镜像源 |
| MinIO 起不来 | `docker compose logs minio`；确认 digest 镜像可用；临时可 `ALGOOS_S3_MODE=moto` |
| `localhost:8080` 打不开 | `local_cluster.sh status`；看 `work/cluster/logs/api.log` |
| `doctor` 对象存储失败 | 检查 9000 端口、MinIO 凭据、bucket 是否可建 |
| 提交一直 `QUEUED` | `algoos nodes` 是否 ALIVE；看 control/compile 日志 |
| 提交一直 `EVALUATING` | 看对应 eval agent/worker 日志；检查缓存/对象下载 |
| 特判一直 `PENDING_SPECIAL` | 检查 special agent、`special.result.<node>`、heartbeat |
| 端口冲突 | 修改 compose 端口和 `local_cluster.sh` 的默认端口 |
| 本机测试出现 OOM/MLE 不稳定 | 默认关闭 cgroup；设置 `ALGOOS_FULL_SANDBOX=1` 仅限特权 Linux/VM |

日志位置：

```text
work/cluster/logs/*.log
docker compose -f deploy/compose/docker-compose.yml logs
```

生成/测试文件清理：

```bash
deploy/scripts/local_cluster.sh clean
rm -rf work /tmp/algoos /tmp/algoos-cache
find work artifacts -name '*.zip' -delete   # 只清理运行/证据目录中的测试打包
```

---

## 5. 当前验收口径

本机部署完成后的准确表述是：

> PostgreSQL、Redis、NATS、真实 MinIO 单容器与原生 C++ Worker 已在本机多进程模式跑通；
> 提交、编译、评测、特判、取消、有序短路、缓存、重派和结果聚合均有本机证据。

不能写成：

> 已通过生产沙箱、KVM 参考多节点、跨机网络/时钟或生产 MinIO 集群验收。

当前证据和未完成项见 [实现状态](docs/status/implementation_status.md)。
