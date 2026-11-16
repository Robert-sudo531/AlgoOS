#!/usr/bin/env bash
# Create the WSL2-KVM experiment VMs: control + eval-01 + eval-02.
# Must run as root inside WSL2 Ubuntu (sudo). Run from a checkout or /mnt/d path.
set -euo pipefail

LAB_USER="${LAB_USER:-xyh}"
LAB_HOME="${LAB_HOME:-/home/${LAB_USER}}"
SSH_PUBKEY="${SSH_PUBKEY:-${LAB_HOME}/.ssh/algoos_lab_ed25519.pub}"
IMAGE_URL="${IMAGE_URL:-https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img}"
IMG_DIR="${IMG_DIR:-/var/lib/libvirt/images/algoos}"
BASE_IMG="${BASE_IMG:-${IMG_DIR}/noble-base.qcow2}"
DISK_SIZE="${DISK_SIZE:-40G}"
NET="${NET:-default}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1"; exit 1; }; }
need virt-install
need qemu-img
need virsh
need cloud-localds
need ssh
need curl

if [ ! -f "${SSH_PUBKEY}" ]; then
  echo "[.] generating lab SSH key for ${LAB_USER}"
  install -d -o "${LAB_USER}" -g "${LAB_USER}" -m 700 "${LAB_HOME}/.ssh"
  sudo -u "${LAB_USER}" ssh-keygen -t ed25519 -N "" \
    -f "${SSH_PUBKEY%.pub}" -C "algoos-lab"
  chown -R "${LAB_USER}:${LAB_USER}" "${LAB_HOME}/.ssh"
fi

mkdir -p "${IMG_DIR}"
if [ ! -f "${BASE_IMG}" ]; then
  echo "[.] downloading Ubuntu 24.04 cloud image"
  curl -L --fail --retry 3 "${IMAGE_URL}" -o "${IMG_DIR}/noble-base.img"
  mv "${IMG_DIR}/noble-base.img" "${BASE_IMG}"
fi
qemu-img info "${BASE_IMG}" | sed -n '1,6p'

echo "[.] starting libvirt network ${NET}"
virsh net-start "${NET}" >/dev/null 2>&1 || true
virsh net-autostart "${NET}" >/dev/null 2>&1 || true

make_seed() {
  local name="$1" ip="$2" role="$3"
  local dir="${IMG_DIR}/seed-${name}"
  mkdir -p "${dir}"

  cat > "${dir}/meta-data" <<META
instance-id: ${name}
local-hostname: ${name}
META

  cat > "${dir}/user-data" <<USERDATA
#cloud-config
hostname: ${name}
fqdn: ${name}.algoos.lab
manage_etc_hosts: true
users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: [sudo, docker]
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat "${SSH_PUBKEY}")
ssh_pwauth: false
apt:
  primary:
    - arches: [default]
      uri: https://mirrors.ustc.edu.cn/ubuntu
  security:
    - arches: [default]
      uri: https://mirrors.ustc.edu.cn/ubuntu
package_update: true
packages:
  - openssh-server
  - qemu-guest-agent
  - chrony
  - sysstat
  - jq
  - curl
  - git
  - build-essential
  - python3
  - python3-pip
  - python3-venv
  - cmake
  - libjson-c-dev
  - docker.io
  - docker-compose-v2
  - nfs-common
USERDATA

  if [ "${role}" = "control" ]; then
    cat >> "${dir}/user-data" <<'USERDATA_NFS'
  - nfs-kernel-server
USERDATA_NFS
  fi

  cat >> "${dir}/user-data" <<'USERDATA_RUN'
runcmd:
  - systemctl enable --now ssh chrony qemu-guest-agent docker
  - usermod -aG docker ubuntu
  - mkdir -p /etc/docker
  - printf '%s\n' '{"registry-mirrors":["https://docker.1ms.run","https://docker.m.daocloud.io","https://docker.xuanyuan.me"]}' > /etc/docker/daemon.json
  - systemctl restart docker
  - sysctl -w vm.swappiness=1
  - timedatectl set-timezone Asia/Shanghai
USERDATA_RUN

  if [ "${role}" = "control" ]; then
    cat >> "${dir}/user-data" <<'USERDATA_CTL'
  - mkdir -p /srv/algoos/submissions /srv/algoos/result /srv/algoos/repo
  - chown -R ubuntu:ubuntu /srv/algoos
  - printf '%s\n' '/srv/algoos 192.168.122.0/24(rw,sync,no_subtree_check,no_root_squash)' >> /etc/exports
  - exportfs -ra
  - systemctl enable --now nfs-kernel-server
USERDATA_CTL
  else
    cat >> "${dir}/user-data" <<'USERDATA_EVAL'
  - mkdir -p /srv/algoos
  - printf '%s\n' '192.168.122.100:/srv/algoos /srv/algoos nfs defaults,_netdev,nofail 0 0' >> /etc/fstab
  - mount -a
USERDATA_EVAL
  fi
}

create_vm() {
  local name="$1" vcpus="$2" mem="$3" cpuset="$4" ip="$5" role="$6" mac="$7"
  local disk="${IMG_DIR}/${name}.qcow2"

  if virsh dominfo "${name}" >/dev/null 2>&1; then
    echo "[=] ${name} already exists, skipping"
    return 0
  fi

  echo "[.] reserving ${ip} for ${mac} (${name})"
  virsh net-update "${NET}" add ip-dhcp-host \
    "<host mac='${mac}' name='${name}' ip='${ip}'/>" \
    --live --config >/dev/null 2>&1 || true

  echo "[.] creating ${name}: ${vcpus} vCPU, ${mem} MiB, ${ip}, cpuset ${cpuset}"
  rm -f "${disk}"
  qemu-img create -f qcow2 -F qcow2 -b "${BASE_IMG}" "${disk}" "${DISK_SIZE}" >/dev/null
  make_seed "${name}" "${ip}" "${role}"

  virt-install --connect qemu:///system --name "${name}" \
    --memory "${mem}" --vcpus "${vcpus}" --cpuset "${cpuset}" \
    --import \
    --disk path="${disk}",format=qcow2,bus=virtio,cache=none \
    --network network="${NET}",model=virtio,mac="${mac}" \
    --os-variant ubuntu24.04 \
    --cloud-init user-data="${IMG_DIR}/seed-${name}/user-data",meta-data="${IMG_DIR}/seed-${name}/meta-data" \
    --graphics vnc,listen=127.0.0.1 \
    --noautoconsole >/dev/null
}

wait_ssh() {
  local ip="$1" name="$2"
  echo -n "[.] waiting for ${name} (${ip}) ssh"
  for _ in $(seq 1 150); do
    if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
         -o ConnectTimeout=2 -o LogLevel=ERROR \
         -i "${SSH_PUBKEY%.pub}" "ubuntu@${ip}" true 2>/dev/null; then
      echo " ok"
      ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o LogLevel=ERROR -i "${SSH_PUBKEY%.pub}" "ubuntu@${ip}" \
        'cloud-init status --wait >/dev/null 2>&1 || true'
      return 0
    fi
    echo -n "."
    sleep 2
  done
  echo " TIMEOUT"
  return 1
}

create_vm algoos-control 4 4096 0-3 192.168.122.100 control 52:54:00:aa:00:10
wait_ssh 192.168.122.100 algoos-control

create_vm algoos-eval-01 4 4096 4-7 192.168.122.101 eval 52:54:00:aa:00:11
wait_ssh 192.168.122.101 algoos-eval-01

create_vm algoos-eval-02 4 4096 8-11 192.168.122.102 eval 52:54:00:aa:00:12
wait_ssh 192.168.122.102 algoos-eval-02

echo
echo "=== VMs ==="
virsh list --all
echo
echo "control : 192.168.122.100  (4 vCPU / 4 GiB, cpuset 0-3)"
echo "eval-01 : 192.168.122.101  (4 vCPU / 4 GiB, cpuset 4-7)"
echo "eval-02 : 192.168.122.102  (4 vCPU / 4 GiB, cpuset 8-11)"
echo "ssh     : ssh -i ${SSH_PUBKEY%.pub} ubuntu@192.168.122.100"
echo "nfs     : /srv/algoos on control, mounted on eval nodes"
