#!/usr/bin/env bash
# Start the libvirt network and the three experiment VMs, then wait for SSH.
set -euo pipefail

KEY="${KEY:-$HOME/.ssh/algoos_lab_ed25519}"
NET="${NET:-default}"

virsh net-start "$NET" >/dev/null 2>&1 || true
virsh net-autostart "$NET" >/dev/null 2>&1 || true

for vm in algoos-control algoos-eval-01 algoos-eval-02; do
  if virsh dominfo "$vm" >/dev/null 2>&1; then
    virsh start "$vm" >/dev/null 2>&1 || true
    virsh autostart "$vm" >/dev/null 2>&1 || true
    echo "started: $vm"
  fi
done

wait_ssh() {
  local ip="$1" name="$2"
  for _ in $(seq 1 60); do
    if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
         -o ConnectTimeout=2 -o LogLevel=ERROR -i "$KEY" "ubuntu@${ip}" true 2>/dev/null; then
      echo "ssh ok: $name ($ip)"
      return 0
    fi
    sleep 2
  done
  echo "ssh timeout: $name ($ip)"
  return 1
}

wait_ssh 192.168.122.100 algoos-control || true
wait_ssh 192.168.122.101 algoos-eval-01 || true
wait_ssh 192.168.122.102 algoos-eval-02 || true

echo
virsh list --all
