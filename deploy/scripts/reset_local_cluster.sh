#!/usr/bin/env bash
# Destructive local reset: stops AlgoOS processes and removes Compose volumes.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
read -r -p "这将删除本机 AlgoOS 容器卷与临时缓存，输入 RESET 继续: " answer
[[ $answer == RESET ]] || { echo "已取消"; exit 1; }
deploy/scripts/local_cluster.sh stop || true
docker compose -f "$ROOT/deploy/compose/docker-compose.yml" --env-file "$ROOT/deploy/compose/.env" down -v || true
rm -rf /tmp/algoos /tmp/algoos-cache "$ROOT/work/cluster"
echo "本地 AlgoOS 状态已清理"
