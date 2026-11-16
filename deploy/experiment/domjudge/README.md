# DOMjudge inside the experiment VMs

拓扑：

- `algoos-control`（192.168.122.100）：MariaDB + domserver，Web 端口 `12345`。
- `algoos-eval-01` / `algoos-eval-02`：各跑一个 `domjudge/judgehost`，向 control 注册。

## control

```bash
ssh -i ~/.ssh/algoos_lab_ed25519 ubuntu@192.168.122.100
mkdir -p ~/domjudge && cd ~/domjudge
# 复制 control-compose.yml 为 docker-compose.yml，并创建 .env
cat > .env <<'EOF'
MYSQL_ROOT_PASSWORD=<random>
MYSQL_PASSWORD=<random>
EOF
docker compose up -d
docker compose logs domserver | grep -i "Initial admin password"
docker compose logs domserver | grep -i "Initial judgehost password"
```

默认管理员 `admin`，密码见启动日志或
`docker compose exec domserver cat /opt/domjudge/domserver/etc/initial_admin_password.secret`。

## eval 节点

把 control 的 judgehost 密码写入 `.env`：

```bash
ssh -i ~/.ssh/algoos_lab_ed25519 ubuntu@192.168.122.101
mkdir -p ~/judgehost && cd ~/judgehost
# 复制 judgehost-compose.yml 为 docker-compose.yml
cat > .env <<'EOF'
JUDGEDAEMON_PASSWORD=<control 日志中的 Initial judgehost password>
DOMSERVER_BASEURL=http://192.168.122.100:12345/
JUDGEHOST_NAME=judgedaemon-eval01
EOF
docker compose up -d
docker compose logs -f
```

eval-02 同理，改 `JUDGEHOST_NAME=judgedaemon-eval02`。

## 从 WSL/Windows 访问 Web

实验时回放器在 WSL 或 control 内运行，直接访问 `http://192.168.122.100:12345/`。如果要从 Windows 浏览器访问，在 WSL 里做端口转发：

```bash
# WSL 内执行；Windows 访问 http://<WSL_IP>:12345/
socat TCP-LISTEN:12345,fork TCP:192.168.122.100:12345
```

或用 SSH 隧道：

```bash
ssh -L 12345:192.168.122.100:12345 -i ~/.ssh/algoos_lab_ed25519 ubuntu@192.168.122.100
```
