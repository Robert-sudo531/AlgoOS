#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y openssh-server qemu-guest-agent chrony sysstat jq curl git \
  build-essential python3 python3-pip python3-venv cmake libjson-c-dev \
  docker.io docker-compose-v2 nfs-common nfs-kernel-server

systemctl enable --now ssh chrony qemu-guest-agent docker
usermod -aG docker ubuntu

mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{"registry-mirrors":["https://docker.1ms.run","https://docker.m.daocloud.io","https://docker.xuanyuan.me"]}
JSON
systemctl restart docker

mkdir -p /srv/algoos/submissions /srv/algoos/result /srv/algoos/repo
chown -R ubuntu:ubuntu /srv/algoos
grep -q '^/srv/algoos ' /etc/exports || echo '/srv/algoos 192.168.122.0/24(rw,sync,no_subtree_check,no_root_squash)' >> /etc/exports
exportfs -ra
systemctl enable --now nfs-kernel-server

sysctl -w vm.swappiness=1
timedatectl set-timezone Asia/Shanghai
echo SETUP_DONE
