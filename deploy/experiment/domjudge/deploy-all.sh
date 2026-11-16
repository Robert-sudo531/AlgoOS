#!/usr/bin/env bash
# Deploy DOMjudge into the WSL2-KVM experiment VMs.
# Run from WSL. Requires ~/.ssh/algoos_lab_ed25519 and the three VMs running.
set -euo pipefail

KEY="${KEY:-$HOME/.ssh/algoos_lab_ed25519}"
CONTROL="${CONTROL:-192.168.122.100}"
EVAL01="${EVAL01:-192.168.122.101}"
EVAL02="${EVAL02:-192.168.122.102}"
BASE="${BASE:-/mnt/d/AlgoOS/deploy/experiment/domjudge}"
SSH=(ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i "$KEY")
SCP=(scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i "$KEY")

echo "=== control: ${CONTROL} ==="
"${SSH[@]}" "ubuntu@${CONTROL}" 'mkdir -p ~/domjudge'
"${SCP[@]}" "${BASE}/control-compose.yml" "ubuntu@${CONTROL}:~/domjudge/docker-compose.yml"

CONTROL_OUT=$("${SSH[@]}" "ubuntu@${CONTROL}" 'bash -s' <<'EOS'
set -euo pipefail
cd "$HOME/domjudge"
if [ ! -f .env ]; then
  umask 077
  cat > .env <<ENV
MYSQL_ROOT_PASSWORD=$(openssl rand -hex 16)
MYSQL_PASSWORD=$(openssl rand -hex 16)
ENV
fi
docker compose up -d
for i in $(seq 1 180); do
  if docker compose logs domserver 2>/dev/null | grep -q 'Initial judgehost password'; then
    break
  fi
  sleep 2
done
docker compose ps
docker compose logs domserver 2>&1 | grep -iE 'Initial (admin|judgehost) password' | tr -d '\r'
EOS
)
echo "$CONTROL_OUT"

JHPW=$(printf '%s\n' "$CONTROL_OUT" | sed -n 's/.*Initial judgehost password is //p' | head -1 | tr -d '\r')
if [ -z "$JHPW" ]; then
  echo "ERROR: could not find judgehost password in domserver logs" >&2
  exit 1
fi

deploy_judgehost() {
  local ip="$1" name="$2" id="$3"
  echo "=== eval ${id}: ${ip} (${name}) ==="
  "${SSH[@]}" "ubuntu@${ip}" 'mkdir -p ~/judgehost'
  "${SCP[@]}" "${BASE}/judgehost-compose.yml" "ubuntu@${ip}:~/judgehost/docker-compose.yml"
  "${SSH[@]}" "ubuntu@${ip}" "bash -s" <<EOS
set -euo pipefail
cd "\$HOME/judgehost"
umask 077
cat > .env <<ENV
JUDGEDAEMON_PASSWORD=${JHPW}
DOMSERVER_BASEURL=http://${CONTROL}:12345/
JUDGEHOST_NAME=${name}
ENV
docker compose up -d
for i in \$(seq 1 120); do
  if docker compose logs judgehost-0 2>/dev/null | grep -q 'No submissions in queue'; then
    break
  fi
  sleep 2
done
docker compose ps
docker compose logs --tail=25 judgehost-0 2>&1 | tr -d '\r'
EOS
}

deploy_judgehost "$EVAL01" "judgedaemon-eval01" "01"
deploy_judgehost "$EVAL02" "judgedaemon-eval02" "02"

echo "=== judgehosts in DB ==="
"${SSH[@]}" "ubuntu@${CONTROL}" 'cd "$HOME/domjudge"; set -a; source .env; set +a; docker compose exec -T mariadb mariadb -udomjudge -p"$MYSQL_PASSWORD" domjudge -e "SELECT hostname,enabled FROM judgehost;"'
