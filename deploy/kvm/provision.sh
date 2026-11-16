#!/usr/bin/env bash
# Provision the AlgoOS reference VMs with libvirt + cloud-init.
# The example below has 1 control + 2 compile + 2 eval + 2 special nodes;
# role counts are not fixed by the scheduler or protocol.
# NOT executed by the local test suite; requires /dev/kvm, libvirt, virt-install,
# qemu-img and cloud-localds on the hypervisor.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
IMAGE=${ALGOOS_CLOUD_IMAGE:-/var/lib/libvirt/images/noble-server-cloudimg-amd64.img}
IMAGE_URL=${ALGOOS_CLOUD_IMAGE_URL:-https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img}
MEMORY=${ALGOOS_VM_MEMORY_MB:-2048}
VCPUS=${ALGOOS_VM_VCPUS:-2}
DISK_GB=${ALGOOS_VM_DISK_GB:-20}
REPO_URL=${ALGOOS_REPO_URL:?set ALGOOS_REPO_URL to the git clone URL}
SSH_KEY=${ALGOOS_SSH_PUBLIC_KEY:?set ALGOOS_SSH_PUBLIC_KEY}
DB_DSN=${ALGOOS_DB_DSN:?set ALGOOS_DB_DSN}
NATS_URL=${ALGOOS_NATS_URL:?set ALGOOS_NATS_URL}
NATS_TOKEN=${ALGOOS_NATS_TOKEN:-}
S3_ENDPOINT=${ALGOOS_S3_ENDPOINT:?set ALGOOS_S3_ENDPOINT}
S3_ACCESS_KEY=${ALGOOS_S3_ACCESS_KEY:?set ALGOOS_S3_ACCESS_KEY}
S3_SECRET_KEY=${ALGOOS_S3_SECRET_KEY:?set ALGOOS_S3_SECRET_KEY}
REDIS_URL=${ALGOOS_REDIS_URL:?set ALGOOS_REDIS_URL}
API_TOKEN=${ALGOOS_API_TOKEN:?set ALGOOS_API_TOKEN}

for tool in virsh virt-install qemu-img cloud-localds; do
  command -v "$tool" >/dev/null || { echo "missing $tool" >&2; exit 1; }
done
[[ -e /dev/kvm ]] || { echo "/dev/kvm is not available" >&2; exit 1; }
[[ -f $IMAGE ]] || { echo "cloud image missing; downloading to $IMAGE"; sudo mkdir -p "$(dirname "$IMAGE")"; sudo curl -L --fail -o "$IMAGE" "$IMAGE_URL"; }

if ! virsh net-info algoos >/dev/null 2>&1; then
  virsh net-define "$ROOT/deploy/kvm/network.xml"
  virsh net-autostart algoos
  virsh net-start algoos
fi

hosts=(algoos-control algoos-compile-01 algoos-compile-02 algoos-eval-01 algoos-eval-02 algoos-special-01 algoos-special-02)
macs=(52:54:00:12:34:10 52:54:00:12:34:11 52:54:00:12:34:12 52:54:00:12:34:21 52:54:00:12:34:22 52:54:00:12:34:31 52:54:00:12:34:32)
for index in "${!hosts[@]}"; do
  name=${hosts[$index]}
  mac=${macs[$index]}
  overlay=/var/lib/libvirt/images/${name}.qcow2
  seed=/var/lib/libvirt/images/${name}-seed.iso
  work=$(mktemp -d)
  sed -e "s|__HOSTNAME__|${name}|g" \
      -e "s|__SSH_PUBLIC_KEY__|${SSH_KEY}|g" \
      -e "s|__REPO_URL__|${REPO_URL}|g" \
      -e "s|__DB_DSN__|${DB_DSN}|g" \
      -e "s|__NATS_URL__|${NATS_URL}|g" \
      -e "s|__NATS_TOKEN__|${NATS_TOKEN}|g" \
      -e "s|__S3_ENDPOINT__|${S3_ENDPOINT}|g" \
      -e "s|__S3_ACCESS_KEY__|${S3_ACCESS_KEY}|g" \
      -e "s|__S3_SECRET_KEY__|${S3_SECRET_KEY}|g" \
      -e "s|__REDIS_URL__|${REDIS_URL}|g" \
      -e "s|__API_TOKEN__|${API_TOKEN}|g" \
      "$ROOT/deploy/cloud-init/user-data.yaml" >"$work/user-data"
  sed -e "s|__HOSTNAME__|${name}|g" "$ROOT/deploy/cloud-init/meta-data.yaml" >"$work/meta-data"
  cloud-localds "$seed" "$work/user-data" "$work/meta-data"
  rm -rf "$work"
  if virsh dominfo "$name" >/dev/null 2>&1; then
    echo "$name already exists; skipping"
    continue
  fi
  qemu-img create -f qcow2 -F qcow2 -b "$IMAGE" "$overlay" "${DISK_GB}G"
  virt-install \
    --name "$name" \
    --memory "$MEMORY" --vcpus "$VCPUS" \
    --disk "$overlay",device=disk,bus=virtio \
    --disk "$seed",device=cdrom \
    --network network=algoos,mac="$mac",model=virtio \
    --os-variant ubuntu24.04 \
    --import --noautoconsole --graphics none
  echo "created $name"
done
echo "waiting for DHCP leases (control may take a few minutes to run cloud-init)"
for name in "${hosts[@]}"; do
  for _ in $(seq 1 60); do
    addr=$(virsh domifaddr "$name" --source lease 2>/dev/null | awk '/ipv4/ {print $4; exit}' | cut -d/ -f1)
    [[ -n $addr ]] && { echo "$name -> $addr"; break; }
    sleep 5
  done
done
echo "Next: verify each node with deploy/scripts/check_health.sh and the inventory."
