# WSL2-KVM 实验节点

在 WSL2 Ubuntu 内用 KVM/QEMU 创建 3 个实验 VM：

| VM | vCPU | 内存 | IP | cpuset |
| --- | ---: | ---: | --- | --- |
| `algoos-control` | 4 | 4 GiB | 192.168.122.100 | 0-3 |
| `algoos-eval-01` | 4 | 4 GiB | 192.168.122.101 | 4-7 |
| `algoos-eval-02` | 4 | 4 GiB | 192.168.122.102 | 8-11 |

- 3 台合计 12 vCPU / 12 GiB，适配当前 WSL 的 16 vCPU / 15 GiB；WSL 的 12-15 号核留给宿主和负载生成器。
- `control` 运行 DOMjudge 的 MariaDB + domserver，以及 AlgoOS 控制面；`eval-*` 运行 judgehost / Eval Worker。
- `control` 通过 NFS 导出 `/srv/algoos`，两个 eval 节点自动挂载，供 AlgoOS 的 `submissions/` 目录使用。
- 全部使用 Ubuntu 24.04 cloud image + cloud-init，无 GUI，SSH 登录。

## 使用

在 WSL 内执行（需要 sudo）：

```bash
sudo apt-get update
sudo apt-get install -y cloud-image-utils
sudo LAB_USER=xyh LAB_HOME=/home/xyh bash /mnt/d/AlgoOS/deploy/experiment/wsl-kvm/create-vms.sh
```

脚本会：

1. 生成 `~/.ssh/algoos_lab_ed25519`（如果不存在）。
2. 下载 Ubuntu 24.04 cloud image 到 `/var/lib/libvirt/images/algoos/`。
3. 启动 libvirt 默认网络。
4. 创建 3 台 VM，安装 Docker、编译工具、chrony、sysstat，并配置 NFS。

创建完成后：

```bash
ssh -i ~/.ssh/algoos_lab_ed25519 ubuntu@192.168.122.100
virsh list --all
```

虚拟机也会出现在 `virt-manager` 的 `QEMU/KVM`（system 连接）中。控制台可用：

```bash
virsh console algoos-control
```

删除：

```bash
sudo bash /mnt/d/AlgoOS/deploy/experiment/wsl-kvm/delete-vms.sh
```

## 日常启停

WSL 重启后，一条命令拉起网络和 3 台 VM：

```bash
bash /mnt/d/AlgoOS/deploy/experiment/wsl-kvm/start-vms.sh
```

三台 VM 已设置 `virsh autostart`，libvirt 启动时会自动拉起；网络 `default` 也已设置 autostart。

## WSL 空闲关闭问题

WSL2 默认在没有任何实例运行约 60 秒后关闭整个 WSL 虚拟机，这会连同 KVM VM 一起停掉。建议在 Windows 用户目录创建/修改 `.wslconfig`：

```ini
[wsl2]
memory=20GB
processors=16
swap=4GB
nestedVirtualization=true
vmIdleTimeout=-1
```

修改后执行（会重启 WSL，VM 会随 autostart 恢复）：

```powershell
wsl --shutdown
```

然后重新打开 Ubuntu，运行 `start-vms.sh`。如果不想改配置，实验期间保持一个 WSL 终端打开即可。

## 注意事项

- 这是嵌套虚拟化：WSL2 本身是 VM，KVM 再跑一层。**只用于 AlgoOS 与 DOMjudge 的相对比较**，绝对性能不能与裸金属数据混写。
- 实验前停止 WSL 里已有的 Docker/DOMjudge（`cd ~/domjudge && docker compose down`），避免争抢 CPU 和内存。
- 当前 WSL 内存为 15 GiB；若要跑 5 VM 完整拓扑，先在 Windows 用户目录创建 `.wslconfig` 提高 `memory`（例如 24GB），再 `wsl --shutdown` 重启 WSL。
- 3 台 VM 的 vCPU 已用 `--cpuset` 分配到互不重叠的 WSL vCPU；但 WSL 的 vCPU 由 Windows 调度，仍可能受宿主负载影响。实验时记录 `%steal`/实际耗时波动。
