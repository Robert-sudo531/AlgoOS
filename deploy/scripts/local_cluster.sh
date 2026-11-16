#!/usr/bin/env bash
# AlgoOS local multi-role cluster: Postgres/Redis/NATS + MinIO + native workers + agents.
# Role counts default to two each and are configurable:
#   ALGOOS_COMPILE_NODES=2 ALGOOS_EVAL_NODES=3 ALGOOS_SPECIAL_NODES=2 ...
# ALGOOS_S3_MODE=minio (default) uses the pinned MinIO image; moto is a fallback
# for environments where that image cannot be pulled.
# Usage: local_cluster.sh start|stop|status|logs|clean
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
WORK=${ALGOOS_WORK:-$ROOT/work/cluster}
LOGS=$WORK/logs
RUN=$WORK/run
CONFIG=$WORK/config
COMPOSE=$ROOT/deploy/compose/docker-compose.yml
BIN=$ROOT/build/cpp
cd "$ROOT"
export PATH="$ROOT/.venv/bin:$HOME/.local/bin:$PATH"
export ALGOOS_DB_DSN=${ALGOOS_DB_DSN:-postgresql://algoos:algoos@127.0.0.1:5432/algoos}
export ALGOOS_NATS_URL=${ALGOOS_NATS_URL:-nats://127.0.0.1:4222}
export ALGOOS_S3_ENDPOINT=${ALGOOS_S3_ENDPOINT:-http://127.0.0.1:9000}
export ALGOOS_S3_ACCESS_KEY=${ALGOOS_S3_ACCESS_KEY:-algoos}
export ALGOOS_S3_SECRET_KEY=${ALGOOS_S3_SECRET_KEY:-algoos-secret}
export ALGOOS_REDIS_URL=${ALGOOS_REDIS_URL:-redis://127.0.0.1:6379/0}
export ALGOOS_API_URL=${ALGOOS_API_URL:-http://127.0.0.1:8080}
# Local development defaults to the portable rlimit path.  Set
# ALGOOS_FULL_SANDBOX=1 on a privileged Linux host/VM to exercise cgroup,
# namespace and seccomp.  Full isolation is validated separately by the C++
# sandbox unit tests and must be revalidated on the target VM.
SANDBOX_FLAGS="--no-cgroup --no-namespaces --no-seccomp --writable-root"
if [[ ${ALGOOS_FULL_SANDBOX:-0} == 1 ]]; then
  SANDBOX_FLAGS=""
fi

log() { printf '[local_cluster] %s\n' "$*"; }

COMPILE_COUNT=${ALGOOS_COMPILE_NODES:-2}
EVAL_COUNT=${ALGOOS_EVAL_NODES:-2}
SPECIAL_COUNT=${ALGOOS_SPECIAL_NODES:-2}
for count in "$COMPILE_COUNT" "$EVAL_COUNT" "$SPECIAL_COUNT"; do
  if ! [[ $count =~ ^[1-9][0-9]*$ ]]; then
    log "node counts must be positive integers"; exit 2
  fi
done
node_ids() {
  local role=$1 count=$2 index
  for ((index = 1; index <= count; index++)); do
    printf '%s-%02d\n' "$role" "$index"
  done
}
mapfile -t COMPILE_NODES < <(node_ids compile "$COMPILE_COUNT")
mapfile -t EVAL_NODES < <(node_ids eval "$EVAL_COUNT")
mapfile -t SPECIAL_NODES < <(node_ids special "$SPECIAL_COUNT")
ALL_NODES=("${COMPILE_NODES[@]}" "${EVAL_NODES[@]}" "${SPECIAL_NODES[@]}")

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
  NODE_COUNTS="$COMPILE_COUNT $EVAL_COUNT $SPECIAL_COUNT" python3 - "$CONFIG" "$RUN" <<'PY'
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1])
run_dir = sys.argv[2]
counts = [int(value) for value in os.environ["NODE_COUNTS"].split()]
for role, count in zip(("compile", "eval", "special"), counts, strict=True):
    for index in range(1, count + 1):
        name = f"{role}-{index:02d}"
        config = {
            "node_id": name,
            "role": role,
            "native_socket": f"{run_dir}/{name}.sock",
            "work_root": f"/tmp/algoos/{name}-agent",
            "cache_root": f"/tmp/algoos-cache/{name}",
            "cache_max_bytes": 2 << 30,
            "max_parallel": 2,
        }
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
  docker compose -f "$COMPOSE" --env-file "$ROOT/deploy/compose/.env" up -d postgres redis nats
  wait_port 127.0.0.1 5432 PostgreSQL
  wait_port 127.0.0.1 6379 Redis
  wait_port 127.0.0.1 4222 NATS
  if [[ ${ALGOOS_S3_MODE:-minio} == moto ]]; then
    # Fallback only: moto is an S3-compatible mock, not a MinIO validation.
    start_bg moto uv run moto_server -H 127.0.0.1 -p 9000
    wait_port 127.0.0.1 9000 "moto S3"
  else
    docker compose -f "$COMPOSE" --env-file "$ROOT/deploy/compose/.env" up -d minio
    for _ in $(seq 1 120); do
      if curl -fsS http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1; then
        log "MinIO ready"
        break
      fi
      sleep 0.5
    done
    curl -fsS http://127.0.0.1:9000/minio/health/live >/dev/null || {
      log "MinIO did not become healthy; docker compose logs minio"; exit 1; }
  fi
  check_binary
  write_configs
  for node in "${COMPILE_NODES[@]}"; do
    start_bg "$node-worker" "$BIN/algoos-compile" --socket "$RUN/$node.sock" \
      --work-root "/tmp/algoos/$node" --workers 2
  done
  for node in "${EVAL_NODES[@]}"; do
    start_bg "$node-worker" "$BIN/algoos-eval" --socket "$RUN/$node.sock" --node-id "$node" \
      --work-root "/tmp/algoos/$node" --workers 2 $SANDBOX_FLAGS
  done
  for node in "${SPECIAL_NODES[@]}"; do
    start_bg "$node-worker" "$BIN/algoos-special" --socket "$RUN/$node.sock" \
      --work-root "/tmp/algoos/$node" --workers 2 $SANDBOX_FLAGS
  done
  for _ in $(seq 1 50); do
    ready=1
    for node in "${ALL_NODES[@]}"; do
      [[ -S "$RUN/$node.sock" ]] || ready=0
    done
    [[ $ready == 1 ]] && break
    sleep 0.2
  done
  # Work roots must exist and be writable by the agents' native workers.
  mkdir -p /tmp/algoos /tmp/algoos-cache
  for node in "${COMPILE_NODES[@]}"; do
    start_bg "$node" uv run algoos compile-agent --config "$CONFIG/$node.json"
  done
  for node in "${EVAL_NODES[@]}"; do
    start_bg "$node" uv run algoos eval-agent --config "$CONFIG/$node.json"
  done
  for node in "${SPECIAL_NODES[@]}"; do
    start_bg "$node" uv run algoos special-agent --config "$CONFIG/$node.json"
  done
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
  local pid_file name
  for pid_file in "$WORK"/*.pid; do
    [[ -e $pid_file ]] || continue
    name=$(basename "$pid_file" .pid)
    stop_one "$name"
  done
  docker compose -f "$COMPOSE" --env-file "$ROOT/deploy/compose/.env" stop || true
  log "cluster stopped (volumes kept)"
}

status() {
  local pid_file name
  for pid_file in "$WORK"/*.pid; do
    [[ -e $pid_file ]] || continue
    name=$(basename "$pid_file" .pid)
    if kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      printf '%-18s running (pid %s)\n' "$name" "$(cat "$pid_file")"
    else
      printf '%-18s stopped\n' "$name"
    fi
  done
  curl -fsS http://127.0.0.1:8080/healthz 2>/dev/null && echo || true
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  logs) tail -n 40 -f "$LOGS"/*.log ;;
  clean) stop; docker compose -f "$COMPOSE" --env-file "$ROOT/deploy/compose/.env" down -v ;;
  *) echo "usage: $0 start|stop|status|logs|clean" >&2; exit 2 ;;
esac
