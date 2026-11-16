# AlgoOS 交接说明

> 更新：2026-11-16
> 分支：`加班加点大赶工`
> 适用对象：接手仓库的开发/运维人员
> 详细验收记录见 [实现状态](implementation_status.md)，部署步骤见 [部署与自测](../../deployment.md)，历史过程见 [开发记录](../records/progress.md)。

## 1. 当前结论

分布式主链路已经在本机多进程环境跑通：

- API → 控制面 → 编译 Worker → 调度器 → 评测 Worker → 聚合器；
- 特判 Worker（评测机上传 `.out`，特判机下载并运行出题者 checker）；
- AC / CE / WA / RE / TLE / SE、用户取消、有序短路；
- 缓存命中、重复/冲突结果幂等、Agent 重启重投、失联节点重派、特判 fallback；
- PostgreSQL、Redis、NATS JetStream、真实 MinIO 容器；
- 调度策略、离线模拟、模拟退火放置和实验运行器。

**未完成**：KVM 五 VM 实际部署、生产沙箱（真实 cgroup OOM、seccomp 逐 syscall）、
跨机网络/时钟、正式重复实验矩阵、Web 浏览器验收、CI、最终 commit/push。

## 2. 目录入口

```text
README.md                         项目总览、快速开始、结构索引
cpp/                              C++23 执行内核、沙箱、Worker 服务、核心测试
python/                           Python 包、Agent、API、CLI、Web、测试
protocol/                         策略、JSON Schema、golden、非法 fixture
migrations/                       PostgreSQL 迁移
deploy/                           Compose、systemd、cloud-init、inventory、KVM、脚本
experiments/                      集群实验、模拟器、SA 放置、分析
problems/                         题目包
examples/                         验证程序
scripts/                          verification 脚本
artifacts/verification/           当前通过的本机验收证据
artifacts/experiments/            当前实验数据
docs/                             需求、设计、运维、状态、记录、报告、归档
```

完整树与命名规范见 [文件结构与命名规范](../development/file_structure.md)。

## 3. 快速验收

前置：Linux、g++ 16+、CMake/Ninja、Docker、Python 3.13+、`uv`。

```bash
uv sync
export PATH="$PWD/.venv/bin:$PATH"
cmake -S cpp -B build/cpp -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/cpp --parallel "$(nproc --ignore=4)"

./build/cpp/algoos-core-tests
uv run pytest -q
uv run ruff check python experiments scripts protocol
```

真实本机集群：

```bash
cp deploy/compose/.env.example deploy/compose/.env
deploy/scripts/local_cluster.sh start
uv run algoos doctor
uv run algoos import-problems
uv run algoos submit --problem a_plus_b --file examples/a_plus_b.cpp
deploy/scripts/local_cluster.sh stop
```

完整集成测试（会启动 PostgreSQL / Redis / NATS / MinIO 容器）：

```bash
uv run pytest -q -m integration
```

## 4. 当前证据

| 内容 | 证据 |
|---|---|
| 本机基线 smoke（六种判定、双节点） | [20261116T082019Z_baseline_smoke](../../artifacts/verification/20261116T082019Z_baseline_smoke/manifest.json) |
| 特判 / 取消 / 有序短路 | [20261116T123748Z_special_flow](../../artifacts/verification/20261116T123748Z_special_flow/manifest.json) |
| 缓存 / 重复结果 / 重启 / 重派 / fallback | [20261116T123819Z_reliability_flow](../../artifacts/verification/20261116T123819Z_reliability_flow/manifest.json) |
| 8 条集成测试（真实 MinIO） | [20261116T123627Z_minio_integration](../../artifacts/verification/20261116T123627Z_minio_integration/manifest.json) |
| 本机真实集群实验（6 条轨迹） | [20261116T122302Z_cluster_cache_aware](../../artifacts/experiments/20261116T122302Z_cluster_cache_aware/manifest.json) |

## 5. 仍然不能声称

1. KVM 五 VM 已部署：只有 `deploy/kvm/` 资产，未在本机执行。
2. 生产级隔离：本机脚本默认关闭 cgroup/namespace/seccomp；C++ 单测只覆盖部分沙箱行为。
3. 真实 cgroup OOM、seccomp 逐 syscall、跨机网络/时钟已验证：均未做。
4. 生产 MinIO 集群：本机是单容器、非持久化/非 TLS 验证。
5. 正式性能结论：目前只有一条 6 记录本机轨迹，没有 3 次以上重复、分位数和方差。
6. Web 浏览器交互、完整 API 契约、CI：未补。

## 6. 常见坑

- **MinIO 镜像**：使用 `cgr.dev/chainguard/minio@sha256:9158c67a...`（Chainguard 构建的
  AGPLv3 社区版）。上游 `minio/minio` 在当前环境被 registry 拒绝，`dl.min.io` 社区
  二进制返回 410；`aistor` 二进制无 license 会拒绝 S3 操作。
- **moto 回退**：`ALGOOS_S3_MODE=moto` / `ALGOOS_TEST_S3=moto` 只用于无法拉取 MinIO
  的环境；moto 结果不能写成 MinIO 验证。
- **生成文件**：测试打包 `*.zip`、构建目录、缓存目录不入库；运行结束后清理 `/work`。
- **未提交工作区**：交接时先看 `git status`；当前分支仍有大量重构改动未提交。
