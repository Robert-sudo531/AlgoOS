# KVM 参考多节点部署

参考拓扑：1 个控制节点 + 2 个编译节点 + 2 个评测节点 + 2 个特判节点。节点数量是
参考值，不是协议或调度上限；增加节点时同步修改 `provision.sh` 的 hosts/macs 和
`network.xml`。

| VM | 角色 | 参考 IP | 运行单元 |
|---|---|---|---|
| `algoos-control` | 控制面 + 基础设施 | 192.168.123.10 | `algoos-api`、`algoos-control`；PostgreSQL/Redis/NATS/MinIO |
| `algoos-compile-01` | 编译池 | 192.168.123.11 | `algoos-compile-worker@compile-01`、`algoos-compile-agent@compile-01` |
| `algoos-compile-02` | 编译池 | 192.168.123.12 | `algoos-compile-worker@compile-02`、`algoos-compile-agent@compile-02` |
| `algoos-eval-01` | 评测池 | 192.168.123.21 | `algoos-eval-worker@eval-01`、`algoos-eval-agent@eval-01` |
| `algoos-eval-02` | 评测池 | 192.168.123.22 | `algoos-eval-worker@eval-02`、`algoos-eval-agent@eval-02` |
| `algoos-special-01` | 特判池 | 192.168.123.31 | `algoos-special-worker@special-01`、`algoos-special-agent@special-01` |
| `algoos-special-02` | 特判池 | 192.168.123.32 | `algoos-special-worker@special-02`、`algoos-special-agent@special-02` |

## 前置条件

- 宿主机支持 KVM（`/dev/kvm`），已安装 `libvirt`、`qemu`、`virt-install`、`cloud-localds`、`qemu-img`。
- 建议宿主机至少 16 GB 内存（默认每 VM 2 GB × 7）。可在脚本中用 `ALGOOS_VM_MEMORY_MB`、`ALGOOS_VM_VCPUS`、`ALGOOS_VM_DISK_GB` 调整。
- 一个可被节点 `git clone` 的仓库 URL，以及 PostgreSQL/NATS/MinIO/Redis 地址与凭据。
- 不要把真实凭据写进仓库；通过环境变量传给 `provision.sh`。

## 运行

```bash
export ALGOOS_REPO_URL=git@github.com:your-org/AlgoOS.git
export ALGOOS_SSH_PUBLIC_KEY="$(cat ~/.ssh/id_ed25519.pub)"
export ALGOOS_DB_DSN='postgresql://algoos:...@192.168.123.10:5432/algoos'
export ALGOOS_NATS_URL='nats://192.168.123.10:4222'
export ALGOOS_S3_ENDPOINT='http://192.168.123.10:9000'
export ALGOOS_S3_ACCESS_KEY='...'
export ALGOOS_S3_SECRET_KEY='...'
export ALGOOS_REDIS_URL='redis://192.168.123.10:6379/0'
export ALGOOS_API_TOKEN='...'
bash deploy/kvm/provision.sh
```

`provision.sh` 会定义 `algoos` libvirt 网络、下载 Ubuntu Noble cloud image、为每台 VM 生成 cloud-init seed，并等待 DHCP 租约。节点首次启动后：

1. `deploy/scripts/install_node.sh` 按 hostname 安装并启用对应 systemd 单元。
2. `deploy/scripts/check_health.sh` 检查 API 与基础设施。
3. 在控制节点执行 `algoos import-problems`，再提交样例。

## 当前验证边界

本仓库的自动化验收在**单机 Docker + 真实 MinIO 容器 + 多进程**上完成；`provision.sh` 是部署资产，未在本机执行多 VM 实测。汇报时必须区分“本机单容器 MinIO 验证”与“KVM 多节点 / 生产 MinIO 集群验证”，不要混写。
