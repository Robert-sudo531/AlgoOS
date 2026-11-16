# AlgoOS 集群部署

本文覆盖**本机多进程**、**systemd/VM** 与 **KVM 参考多节点** 三种部署方式。所有命令从
仓库根目录执行，Python 使用 `uv`。

## 1. 本机多进程（当前已验证）

### 前置

- Linux + Docker（cgroup v2）
- g++ 16+、CMake/Ninja（`uv tool install cmake ninja` 或 `.venv/bin`）
- Python 3.13+、`uv`
- 可访问的镜像源；本环境使用 `docker.m.daocloud.io`

### 启动

```bash
uv sync
export PATH="$PWD/.venv/bin:$PATH"
cmake -S cpp -B build/cpp -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/cpp --parallel "$(nproc --ignore=4)"

# 基础设施：PostgreSQL / Redis / NATS / MinIO
deploy/scripts/local_cluster.sh start
# 浏览器 http://127.0.0.1:8080/
```

`local_cluster.sh` 默认关闭 cgroup/namespace/seccomp；设置
`ALGOOS_FULL_SANDBOX=1` 可在特权 Linux/VM 上开启完整沙箱。S3 默认使用固定 digest 的
MinIO（Chainguard 构建的 AGPLv3 社区版，`RELEASE.2026-11-12T01-15-23Z`）；上游
`minio/minio` 镜像在当前环境被 registry 拒绝，`dl.min.io` 社区二进制返回 410，
因此没有使用上游镜像。`ALGOOS_S3_MODE=moto` 是回退路径，moto 结果只能写成
“S3 兼容测试”，不能写成 MinIO 验证。无论哪种，本机结果都不是生产对象存储、
跨机或持久化验收。

### 最小验收

```bash
PYTHONPATH=python uv run python scripts/verify_special_flow.py --actor "$USER"
PYTHONPATH=python uv run python scripts/verify_reliability_flow.py --actor "$USER"
uv run pytest -q -m integration
```

## 2. systemd 节点

单元文件在 `deploy/systemd/`，环境模板 `deploy/env.example`：

```bash
sudo install -d /etc/algoos /etc/algoos/nodes /var/lib/algoos /var/cache/algoos
sudo cp deploy/env.example /etc/algoos/algoos.env
sudo chown root:algoos /etc/algoos/algoos.env && sudo chmod 0640 /etc/algoos/algoos.env
sudo install -m 0644 deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

按角色启用：

| 节点 | 命令示例 |
|---|---|
| 控制 | `systemctl enable --now algoos-api algoos-control` |
| 编译池 | `systemctl enable --now algoos-compile-worker@compile-01 algoos-compile-agent@compile-01` |
| 评测池 | `systemctl enable --now algoos-eval-worker@eval-01 algoos-eval-agent@eval-01` |
| 特判池 | `systemctl enable --now algoos-special-worker@special-01 algoos-special-agent@special-01` |

`compile-NN`、`eval-NN`、`special-NN` 都是模板单元；把 `%i` 换成节点 ID 即可增加节点。
`install_node.sh` 会根据主机名自动选择 ID，编译/特判 Agent 与 Native Worker 使用同 UID
（Native Socket 为 `0600`）。

节点 Agent 配置在 `/etc/algoos/nodes/<node>.json`，示例：
`deploy/scripts/install_node.sh` 会按 hostname 生成。原生评测/特判 Worker 需要
root/cgroup 权限；对应 Agent 也按 root 运行，因为它们要连接 `0600` 的 Native Socket。
编译 Agent 与编译 Worker 使用普通用户 `algoos` 即可。

## 3. cloud-init / KVM 参考多节点

- 通用 cloud-init：`deploy/cloud-init/user-data.yaml`（替换凭据与仓库 URL）
- 角色清单：`deploy/inventory/nodes.example.yaml`
- 多节点参考脚本：`deploy/kvm/provision.sh` + `deploy/kvm/network.xml`

```bash
export ALGOOS_REPO_URL=... ALGOOS_SSH_PUBLIC_KEY=... ALGOOS_DB_DSN=... ...
bash deploy/kvm/provision.sh
```

参考拓扑为 1 control + 2 compile + 2 eval + 2 special；节点数量可继续扩展。
资源要求见 [deploy/kvm/README.md](../../deploy/kvm/README.md)。**当前未在本机执行
多 VM**；该脚本是部署资产，不是已完成的云端验收。

## 4. 健康检查

```bash
deploy/scripts/check_health.sh
uv run algoos doctor
uv run algoos nodes
```

`doctor` 检查 PostgreSQL、S3、NATS、Redis TCP 连通性；`nodes` 读取心跳表。

## 5. 实验

```bash
PYTHONPATH=python uv run python -m experiments.runner --count 12 --actor "$USER"
uv run python -m experiments.simulator.run --profile mixed --count 200
uv run python -m experiments.placement.run
uv run python -m experiments.analysis.report artifacts/experiments/<run>
```

真实集群与模拟数据分别写入 `artifacts/experiments/cluster_*` 与
`simulation_*`，manifest 的 `data_source` 是唯一口径。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| Docker Hub 拉取超时 | 使用镜像源；`deploy/compose/.env` 支持 `*_IMAGE` 覆盖 |
| MinIO 镜像不可用 | 用 `cgr.dev/chainguard/minio@sha256:9158c67a...`；仅临时调试可 `ALGOOS_S3_MODE=moto`，结果不能标为 MinIO 验证 |
| cgroup 权限不足 | 以 root 或正确委派 cgroup；本地脚本默认关闭完整沙箱 |
| 端口冲突 | 修改 `local_cluster.sh`/Compose 端口；测试 harness 使用随机端口 |
| NATS 重投慢 | 调小 `ALGOOS_EVAL_ACK_WAIT`/`ALGOOS_CONTROL_ACK_WAIT`（秒） |
| 节点失联不重派 | 检查 `ALGOOS_STALE_SECONDS`、`ALGOOS_REAPER_INTERVAL` 与 heartbeat |
| 特判一直 PENDING_SPECIAL | 检查 `special-result-<node>` durable、Redis heartbeat、`session_id` |
| 结果重复 | 以 `judge_id` 幂等；查看 `processed_messages` 与聚合器日志 |

## 7. 安全

- `ALGOOS_API_TOKEN` 非空时 API 要求 Bearer；公网部署再加 TLS/反向代理。
- `/etc/algoos/algoos.env` 不要提交；Compose `.env` 已加入 `.gitignore`。
- 原生评测 Worker 需要 root 才能使用 cgroup/namespace/seccomp；不要把它暴露到公网。
- 特判 checker 来自题包，仍按不可信代码在沙箱中运行。
