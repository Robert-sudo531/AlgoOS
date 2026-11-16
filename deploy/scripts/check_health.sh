#!/usr/bin/env bash
# Health check for a local or VM AlgoOS node.
set -uo pipefail
API=${ALGOOS_API_URL:-http://127.0.0.1:8080}
fail=0
check() { if "$@" >/dev/null 2>&1; then echo "[通过] $*"; else echo "[失败] $*"; fail=1; fi; }
check curl -fsS "$API/healthz"
if command -v pg_isready >/dev/null; then check pg_isready -h 127.0.0.1 -p 5432; fi
if command -v redis-cli >/dev/null; then check redis-cli -h 127.0.0.1 ping; fi
if systemctl list-unit-files 'algoos-*' >/dev/null 2>&1; then
  for unit in $(systemctl list-units --type=service --state=running --no-legend 'algoos-*' | awk '{print $1}'); do
    echo "[运行] $unit"
  done
fi
exit "$fail"
