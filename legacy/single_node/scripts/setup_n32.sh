#!/usr/bin/env bash
# Ubuntu 26.04/x86_64：只下载和解包，不安装系统软件。
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
root=$PWD
runtime="$root/work/n32_runtime"
tag=RELEASE.2025-10-15T17-29-55Z
revision=9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a
requirements="$root/deploy/n32/requirements.txt"
identity="$revision $(sha256sum < "$requirements" | cut -d ' ' -f 1)"

if [[ -f "$runtime/ready" && $(< "$runtime/ready") == "$identity" && -x "$runtime/venv/bin/python" && -x "$runtime/bin/minio" ]]; then
    printf 'N3.2 依赖已就绪：%s\n' "$runtime"
    exit 0
fi
if ! (source /etc/os-release; [[ $ID == ubuntu && $VERSION_ID == 26.04 ]]) || [[ $(uname -m) != x86_64 ]]; then
    printf '自动准备仅支持 Ubuntu 26.04 x86_64；可用 --config 指定已安装的本机服务。\n' >&2
    exit 1
fi
if [[ $(id -u) == 0 ]]; then
    printf '请以普通用户执行，不要使用 sudo。\n' >&2
    exit 1
fi

mkdir -p "$runtime/debs" "$runtime/toolroot" "$runtime/bin"
if [[ ! -f "$runtime/debs-ready" ]]; then
    printf '下载 Ubuntu 软件包并解包到 work/n32_runtime（第一次耗时较长）…\n'
    (
        cd -- "$runtime/debs"
        packages=(postgresql-18 postgresql-client-18 libpq5 nats-server golang-1.26-go golang-1.26-src)
        apt-get download "${packages[@]}"
        for name in "${packages[@]}"; do
            candidate=$(apt-cache policy "$name" | awk '/Candidate:/ {print $2; exit}')
            package=$(find . -maxdepth 1 -name "${name}_${candidate}_*.deb" -print -quit)
            [[ -n "$package" ]]
            dpkg-deb -x "$package" "$runtime/toolroot"
        done
    )
    touch "$runtime/debs-ready"
fi

if [[ ! -f "$runtime/minio-ready" || $(< "$runtime/minio-ready") != "$revision" || ! -x "$runtime/bin/minio" ]]; then
    if [[ ! -d "$runtime/minio-source" ]]; then
        git clone --quiet --depth 1 --branch "$tag" https://github.com/minio/minio.git "$runtime/minio-source"
    fi
    [[ $(git -C "$runtime/minio-source" rev-parse HEAD) == "$revision" ]]
    printf '构建 MinIO %s…\n' "$tag"
    (
        cd -- "$runtime/minio-source"
        ldflags="-X github.com/minio/minio/cmd.Version=${tag#RELEASE.} -X github.com/minio/minio/cmd.ReleaseTag=$tag -X github.com/minio/minio/cmd.CommitID=$revision"
        env GOENV=off GOTOOLCHAIN=local CGO_ENABLED=0 GOSUMDB=sum.golang.org GOMAXPROCS=4 \
            GOPROXY="${GOPROXY:-https://proxy.golang.org}" GOMODCACHE="$runtime/go-mod" GOCACHE="$runtime/go-build" \
            "$runtime/toolroot/usr/lib/go-1.26/bin/go" build -mod=readonly -p 4 -ldflags "$ldflags" -o "$runtime/bin/minio" .
    )
    printf '%s\n' "$revision" > "$runtime/minio-ready"
fi
python3 -m venv "$runtime/venv"
"$runtime/venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir -r "$requirements"
printf '%s\n' "$identity" > "$runtime/ready"
printf 'N3.2 依赖已就绪。\n'
