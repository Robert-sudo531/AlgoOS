#!/usr/bin/env bash
# Install and enable AlgoOS units on a cloud-init/KVM node. Run as root.
set -euo pipefail
ROOT=${ALGOOS_ROOT:-/opt/algoos}
HOST=$(hostname -s)
case "$HOST" in
  algoos-control) NODE_KIND=control ;;
  algoos-compile) NODE_KIND=compile; NODE=compile-01 ;;
  algoos-compile-*) NODE_KIND=compile; NODE=${HOST#algoos-} ;;
  algoos-eval-*) NODE_KIND=eval; NODE=${HOST#algoos-} ;;
  algoos-special) NODE_KIND=special; NODE=special-01 ;;
  algoos-special-*) NODE_KIND=special; NODE=${HOST#algoos-} ;;
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

case "$NODE_KIND" in
  control)
    systemctl enable --now algoos-api.service algoos-control.service
    ;;
  compile)
    ensure_node_config "$NODE" compile "/run/algoos/${NODE}.sock"
    systemctl enable --now "algoos-compile-worker@${NODE}.service" \
                         "algoos-compile-agent@${NODE}.service"
    ;;
  eval)
    ensure_node_config "$NODE" eval "/run/algoos/${NODE}.sock"
    systemctl enable --now "algoos-eval-worker@${NODE}.service" \
                         "algoos-eval-agent@${NODE}.service"
    ;;
  special)
    ensure_node_config "$NODE" special "/run/algoos/${NODE}.sock"
    systemctl enable --now "algoos-special-worker@${NODE}.service" \
                         "algoos-special-agent@${NODE}.service"
    ;;
esac
echo "AlgoOS ${NODE_KIND} node installed on ${HOST}"
