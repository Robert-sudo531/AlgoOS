# DOMjudge 部署与使用（WSL2 + Docker）

本项目以 **DOMjudge 9.0.1** 作为对照评测系统。它部署在本机 WSL2 的 Ubuntu 发行版内，由 Docker Engine 运行官方镜像；Web 界面从 Windows 浏览器访问 `http://localhost:12345`。

与 AlgoOS 自带评测守护进程（见 [deployment.md](deployment.md)）的分工：

- **AlgoOS**：本地轻量评测原型，题目放在 `problems/`，提交落在 `submissions/`。
- **DOMjudge**：完整的比赛平台，含 Web 界面、队伍/比赛管理、REST API、cgroup 隔离评测，作为功能与判定口径的对照实现。

---

## 1. 部署架构

```text
Windows 11
└── WSL2: Ubuntu 26.04 (systemd)
    └── Docker Engine 29.1.3
        ├── domjudge-mariadb-1    mariadb:11.4          数据库（named volume）
        ├── domjudge-domserver-1  domjudge/domserver:9.0.1  Web + REST API，端口 12345->80
        └── domjudge-judgehost-1  domjudge/judgehost:9.0.1  评测机（privileged + cgroup v2）
```

- 部署目录：WSL 内 `~/domjudge`（`docker-compose.yml` 与 `.env`）。
- 容器网络：`domjudge_dj`；数据库数据卷：`domjudge_mariadb-data`。
- 镜像加速写在 WSL 的 `/etc/docker/daemon.json`，便于从国内拉取 Docker Hub 镜像。
- judgehost 使用 `privileged: true`、`cgroup: host` 并挂载 `/sys/fs/cgroup`；WSL 内核 6.18 的 cgroup v2 已通过验证。

本次部署的辅助脚本位于仓库 `work/domjudge-deploy/`（运行时目录，不提交）。

---

## 2. 日常启停

先确保 WSL 发行版已启动（Windows 重启后第一次使用）：

```bash
wsl -d Ubuntu
```

在 WSL 内：

```bash
cd ~/domjudge
docker compose up -d          # 启动全部服务
docker compose ps             # 查看状态
docker compose logs -f judgehost
docker compose down           # 停止并删除容器，保留数据库数据
```

在 Windows PowerShell 里也可以一条命令启动：

```powershell
wsl -d Ubuntu -- bash -lc "cd ~/domjudge && docker compose up -d"
```

首次启动后 domserver 需要几十秒初始化数据库；`docker compose ps` 显示 `(healthy)` 即可访问。

---

## 3. Web 界面与账号

浏览器打开：

```text
http://localhost:12345
```

### admin 账号

初始密码在 domserver 首次启动时生成，可这样取回：

```bash
cd ~/domjudge
docker compose logs domserver | grep -i "Initial admin password"
docker compose exec domserver cat /opt/domjudge/domserver/etc/initial_admin_password.secret
```

重置任意用户密码（不传新密码则生成随机密码并打印）：

```bash
cd ~/domjudge
docker compose exec domserver /opt/domjudge/domserver/bin/dj_console \
  domjudge:reset-user-password admin 'NewPassword'
```

### 队伍账号

示例数据自带 `demo` 用户（队伍 `Example teamname`，队伍 ID `exteam`）。本次部署已把它设置为：

```text
用户名：demo
密码：demo-pass-2026
```

正式使用前建议重置：

```bash
cd ~/domjudge
docker compose exec domserver /opt/domjudge/domserver/bin/dj_console \
  domjudge:reset-user-password demo
```

### 主要页面

| 页面 | 地址 | 用途 |
| --- | --- | --- |
| 队伍首页 | `/` | 登录、看题、提交、查看结果 |
| 评测管理 | `/jury` | 比赛、题目、提交、队伍、语言、judgehost |
| API 文档 | `/api` | REST API 交互式说明 |

---

## 4. 比赛、题目与语言

示例数据包含 **Demo contest**（`demo`）和 3 道题：

| 题号 | API 题目 ID | 名称 |
| --- | --- | --- |
| A | `hello` | Hello World |
| B | `fltcmp` | Float special compare test |
| C | `boolfind` | Boolean switch search |

- 比赛时间在部署时生成，约 5 小时窗口。过期或需要新比赛：Jury → Contests 修改时间，或新建比赛。
- 当前已启用提交/评测的语言：**C、C++、Java、Python 3**。其余语言在 Jury → Languages 里勾选启用。
- 导入题目：Jury → Problems → Import problem，上传 DOMjudge problem package zip（至少包含 `problem.yaml` 与 `data/sample/*.in/.out`，可含 `data/secret/`、`submissions/`、题面）。

---

## 5. 提交与查看结果

### Web 提交

用队伍账号登录 `http://localhost:12345`，选择题目、语言，粘贴或上传源码后提交；结果在提交列表和 scoreboard 中查看。

### API 提交

以 `demo` 为例：

```bash
curl -u demo:demo-pass-2026 -X POST \
  -F problem_id=hello \
  -F language_id=cpp \
  -F code=@main.cpp \
  http://localhost:12345/api/v4/contests/demo/submissions
```

查询题目、提交：

```bash
curl -u demo:demo-pass-2026 http://localhost:12345/api/v4/contests/demo/problems
curl -u demo:demo-pass-2026 http://localhost:12345/api/v4/contests/demo/submissions/1
```

管理员查看数据库中的判定（也可在 Jury → Submissions 看）：

```bash
cd ~/domjudge
docker compose exec -T mariadb mariadb -udomjudge -p"$(grep '^MYSQL_PASSWORD=' .env | cut -d= -f2)" domjudge \
  -e "SELECT submitid,result FROM judging ORDER BY judgingid DESC LIMIT 5;"
```

本次部署的验证记录：

| 提交 | 内容 | 判定 |
| --- | --- | --- |
| 1 | C++ 输出 `Hello world!` | `correct`（0.002s） |
| 2 | C++ 输出 `Hello` | `wrong-answer` |

---

## 6. 数据、密码与备份

- `.env`（位于 `~/domjudge`，权限 600）保存 MySQL 密码与 judgehost API 密码，**不要提交到仓库**。
- judgehost 的密码必须与 domserver 的 `restapi.secret` 一致；修改后同步更新 `.env` 并 `docker compose up -d judgehost`。
- 数据库数据在 Docker 卷 `domjudge_mariadb-data`：
  - `docker compose down`：保留数据。
  - `docker compose down -v`：删除数据（慎用）。
- 备份数据库：

```bash
cd ~/domjudge
docker compose exec -T mariadb mariadb-dump -udomjudge -p"$(grep '^MYSQL_PASSWORD=' .env | cut -d= -f2)" domjudge > ~/domjudge-backup.sql
```

---

## 7. 常见问题

### `http://localhost:12345` 打不开

1. 确认 WSL 已启动：`wsl -d Ubuntu -- docker compose -f ~/domjudge/docker-compose.yml ps`。
2. 在 WSL 内自测：`curl -I http://localhost:12345/`，应返回 302。
3. Windows 侧仍不通时重启 WSL：`wsl --shutdown`，再重新打开 Ubuntu 并启动容器。
4. 检查 Windows 防火墙是否拦截 WSL 的端口转发。

### domserver 一直是 `starting` / unhealthy

```bash
cd ~/domjudge
docker compose logs --tail=100 domserver
docker compose logs --tail=50 mariadb
```

多为数据库尚未就绪，等待或重启 domserver 即可。

### judgehost 注册失败或反复重启

```bash
docker compose logs --tail=100 judgehost
```

确认 `.env` 里的 `JUDGEDAEMON_PASSWORD` 与 domserver 生成的 judgehost 密码一致；确认 compose 中保留了 `privileged: true`、`cgroup: host` 和 `/sys/fs/cgroup` 挂载。

### 镜像拉取慢或失败

检查 WSL 内 `/etc/docker/daemon.json` 的 `registry-mirrors`，然后：

```bash
sudo systemctl restart docker
```

### 磁盘占用

```bash
docker system df
```

---

## 相关文件

- `~/domjudge/docker-compose.yml`（WSL 内实际部署）
- `~/domjudge/.env`（WSL 内密码配置）
- [work/domjudge-deploy/docker-compose.yml](../../work/domjudge-deploy/docker-compose.yml)（部署模板）
- [AlgoOS 部署与使用](deployment.md)
