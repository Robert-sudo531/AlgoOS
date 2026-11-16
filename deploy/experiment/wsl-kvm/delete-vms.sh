#!/usr/bin/env bash
# Destroy the WSL2-KVM experiment VMs and their disks.
set -euo pipefail
IMG_DIR="${IMG_DIR:-/var/lib/libvirt/images/algoos}"
for name in algoos-control algoos-eval-01 algoos-eval-02; do
  virsh destroy "${name}" >/dev/null 2>&1 || true
  virsh undefine "${name}" --remove-all-storage >/dev/null 2>&1 || true
  rm -rf "${IMG_DIR}/seed-${name}"
done
virsh list --all
