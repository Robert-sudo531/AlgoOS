#!/usr/bin/env bash
# AlgoOS local five-role cluster: Postgres/Redis/MinIO/NATS + native workers + agents.
# Usage: local_cluster.sh start|stop|status|logs|clean|smoke
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
WORK=${ALGOOS_WORK:-$ROOT/work/cluster}
LOGS=$WORK/logs
RUN=$WORK/run
CONFIG=$WORK/config
COMPOSE=$ROOT/deploy/compose/docker-compose.yml
BIN=$ROOT/build/cpp
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
export ALGOOS_DB_DSN=${ALGOOS_DB_DSN:-postgresql://algoos:algoos@127.0.0.1:5432/algoos}
export ALGOOS_NATS_URL=${ALGOOS_NATS_URL:-nats://127.0.0.1:4222}
export ALGOOS_S3_ENDPOINT=${ALGOOS_S3_ENDPOINT:-http://127.0.0.1:9000}
export ALGOOS_S3_ACCESS_KEY=${ALGOOS_S3_ACCESS_KEY:-algoos}
export ALGOOS_S3_SECRET_KEY=${ALGOOS_S3_SECRET_KEY:-algoos-secret}
export ALGOOS_REDIS_URL=${ALGOOS_REDIS_URL:-redis://127.0.0.1:6379/0}
export ALGOOS_API_URL=${ALGOOS_API_URL:-http://127.0.0.1:8080}
SANDBOX_FLAGS=""
if [[ $(id -u) != 0 ]]; then
  SANDBOX_FLAGS="--no-cgroup --no-namespaces --no-seccomp --writable-root"
fi

log() { printf '[local_cluster] %s\n' "$*"; }

wait_port() {
  local host=$1 port=$2 label=$3
  for _ in $(seq 1 120); do
    if (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; then exec 3>&-; log "$label ready"; return 0; fi
    sleep 0.5
  done
  log "$label did not open $host:$port"; return 1
}

start_bg() {
  local name=$1; shift
  nohup "$@" >>"$LOGS/$name.log" 2>&1 &
  echo $! >"$WORK/$name.pid"
  log "started $name (pid $(cat "$WORK/$name.pid"))"
}

stop_one() {
  local name=$1
  local pid_file="$WORK/$name.pid"
  [[ -f $pid_file ]] || return 0
  local pid; pid=$(cat "$pid_file")
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
    kill -9 "$pid" 2>/dev/null || true
    log "stopped $name"
  fi
  rm -f "$pid_file"
}

write_configs() {
  mkdir -p "$CONFIG"
  python3 - "$CONFIG" "$RUN" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
run_dir = sys.argv[2]
configs = {
    "compile-01": dict(node_id="compile-01", role="compile", native_socket="compile.sock",
                       max_parallel=2),
    "eval-01": dict(node_id="eval-01", role="eval", native_socket="eval-01.sock",
                    max_parallel=2),
    "eval-02": dict(node_id="eval-02", role="eval", native_socket="eval-02.sock",
                    max_parallel=2),
    "special-01": dict(node_id="special-01", role="special", native_socket="special-01.sock",
                       max_parallel=2),
}
for name, config in configs.items():
    config["native_socket"] = f"{run_dir}/{config['native_socket']}"
    config["work_root"] = f"/tmp/algoos/{name}-agent"
    config["cache_root"] = f"/tmp/algoos-cache/{name}"
    config["cache_max_bytes"] = 2 << 30
    (root / f"{name}.json").write_text(json.dumps(config, indent=2) + "\n")
PY
}

check_binary() {
  [[ -x $BIN/algoos-eval && -x $BIN/algoos-compile && -x $BIN/algoos-special ]] || {
    log "building C++ workers"
    cmake -S "$ROOT/cpp" -B "$ROOT/build/cpp" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo >/dev/null
    cmake --build "$ROOT/build/cpp" --parallel 8
  }
}

start() {
  mkdir -p "$LOGS" "$RUN" "$CONFIG"
  chmod 755 "$RUN" || true
  log "starting infrastructure"
  docker compose -f "$COMPOSE" up -d
  wait_port 127.0.0.1 5432 PostgreSQL
  wait_port 127.0.0.1 6379 Redis
  wait_port 127.0.0.1 4222 NATS
  wait_port 127.0.0.1 9000 MinIO
  check_binary
  write_configs
  start_bg compile-worker "$BIN/algoos-compile" --socket "$RUN/compile.sock" \
    --work-root /tmp/algoos/compile-01 --workers 2
  for node in eval-01 eval-02; do
    start_bg "$node-worker" "$BIN/algoos-eval" --socket "$RUN/$node.sock" --node-id "$node" \
      --work-root "/tmp/algoos/$node" --workers 2 $SANDBOX_FLAGS
  done
  start_bg special-worker "$BIN/algoos-special" --socket "$RUN/special-01.sock" \
    --work-root /tmp/algoos/special-01 --workers 2 $SANDBOX_FLAGS
  for _ in $(seq 1 50); do
    [[ -S $RUN/compile.sock && -S $RUN/eval-01.sock && -S $RUN/eval-02.sock && -S $RUN/special-01.sock ]] && break
    sleep 0.2
  done
  # Work roots must exist and be writable by the agents' native workers.
  mkdir -p /tmp/algoos /tmp/algoos-cache
  start_bg compile-agent uv run algoos compile-agent --config "$CONFIG/compile-01.json"
  start_bg eval-01 uv run algoos eval-agent --config "$CONFIG/eval-01.json"
  start_bg eval-02 uv run algoos eval-agent --config "$CONFIG/eval-02.json"
  start_bg special-01 uv run algoos special-agent --config "$CONFIG/special-01.json"
  start_bg control uv run algoos control
  sleep 2
  start_bg api uv run algoos api --host 127.0.0.1 --port 8080
  for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then break; fi
    sleep 0.5
  done
  log "cluster ready: http://127.0.0.1:8080/  logs: $LOGS"
}

stop() {
  for name in api control special-01 eval-02 eval-01 compile-agent \
              special-worker eval-02-worker eval-01-worker compile-worker; do
    stop_one "$name"
  done
  docker compose -f "$COMPOSE" stop || true
  log "cluster stopped (volumes kept)"
}

status() {
  for name in compile-worker eval-01-worker eval-02-worker special-worker \
              compile-agent eval-01 eval-02 special-01 control api; do
    local pid_file="$WORK/$name.pid"
    if [[ -f $pid_file ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      printf '%-16s running (pid %s)\n' "$name" "$(cat "$pid_file")"
    else
      printf '%-16s stopped\n' "$name"
    fi
  done
  curl -fsS http://127.0.0.1:8080/healthz 2>/dev/null && echo || true
}

case "${1:-start}" in
  smoke) exec "${ALGOOS_PYTHON:-$ROOT/work/refactor-venv/bin/python}" -m scripts.refactor_smoke "${@:2}" ;;
  start) start ;;
  stop) stop ;;
  status) status ;;
  logs) tail -n 40 -f "$LOGS"/*.log ;;
  clean) stop; docker compose -f "$COMPOSE" down -v ;;
  *) echo "usage: $0 start|stop|status|logs|clean|smoke" >&2; exit 2 ;;
esac
