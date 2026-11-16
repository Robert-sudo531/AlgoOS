#!/usr/bin/env bash
# Install and enable AlgoOS units on a cloud-init/KVM node. Run as root.
set -euo pipefail
ROOT=${ALGOOS_ROOT:-/opt/algoos}
HOST=$(hostname -s)
case "$HOST" in
  algoos-control) ROLE=control ;;
  algoos-compile) ROLE=compile ;;
  algoos-eval-01|algoos-eval-02) ROLE=eval; NODE=${HOST#algoos-} ;;
  algoos-special) ROLE=special ;;
  *) echo "unknown AlgoOS hostname: $HOST" >&2; exit 2 ;;
esac

install -d -o algoos -g algoos /var/lib/algoos /var/cache/algoos /etc/algoos/nodes
install -m 0644 "$ROOT"/deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload

ensure_node_config() {
  local node_id=$1 role=$2 socket=$3
  local file="/etc/algoos/nodes/${node_id}.json"
  if [[ ! -f $file ]]; then
    cat >"$file" <<JSON
{
  "node_id": "${node_id}",
  "role": "${role}",
  "native_socket": "${socket}",
  "work_root": "/var/lib/algoos/${node_id}",
  "cache_root": "/var/cache/algoos/${node_id}",
  "cache_max_bytes": 4294967296,
  "max_parallel": 2
}
JSON
    chown root:algoos "$file"
    chmod 0640 "$file"
  fi
}

case "$ROLE" in
  control)
    systemctl enable --now algoos-api.service algoos-control.service
    ;;
  compile)
    ensure_node_config compile-01 compile /run/algoos/compile.sock
    systemctl enable --now algoos-compile-worker.service algoos-compile-agent.service
    ;;
  eval)
    ensure_node_config "$NODE" eval "/run/algoos/${NODE}.sock"
    systemctl enable --now "algoos-eval-worker@${NODE}.service" \
                         "algoos-eval-agent@${NODE}.service"
    ;;
  special)
    ensure_node_config special-01 special /run/algoos/special.sock
    systemctl enable --now algoos-special-worker.service algoos-special-agent.service
    ;;
esac
echo "AlgoOS ${ROLE} node installed on ${HOST}"
