# AlgoOS 实现状态

> 更新：2026-11-16
> 分支：`加班加点大赶工`
> 结论：**本机分布式闭环通过；KVM/生产隔离/跨机实验未完成。**
> 需求来源：[实施总案](../requirements/distributed_implementation_plan.md)、
> [目标 UML/接口](../requirements/next_step.md)。

## 1. 状态总表

| 模块 | 状态 | 说明 |
|---|---|---|
| 协议 v1 | ✅ 本机通过 | 六类消息、严格 JSON、golden/非法 fixture、JSON Schema |
| API / 控制面 | ✅ 本机通过 | 提交、查询、取消、节点、题目、实验、静态页面 |
| 编译 / 评测 / 特判 | ✅ 本机 2/2/2 通过 | 三种角色均为可扩展 Worker 池；特判 `.out` + checker |
| 判定 | ✅ 本机通过 | AC/CE/WA/RE/TLE/WR/SE/CANCELLED；输出超限统一为 WR/OUTPUT_LIMIT |
| 取消 / 有序短路 | ✅ 本机 E2E | 测试点收敛到 `CANCELLED`；短路边界按序号 |
| 缓存 / 可靠性 | ✅ 本机 E2E | cache_hit、重复/冲突结果、Agent 重启、reaper、fallback |
| 对象存储 | ✅ 本机 MinIO | 单容器、非生产持久化/TLS/集群验证 |
| 沙箱 | ◐ 单测 | rlimit/namespace/cgroup/seccomp 已接入；真实 OOM/逐 syscall 未验证 |
| 调度 / 实验 | ◐ 小规模 | 五种策略、模拟器、SA 放置；无正式重复矩阵 |
| 部署资产 | ◐ 已备 | systemd/cloud-init/inventory/KVM；参考多节点未执行 |
| Web / API 契约 / CI | ❌ 未完成 | 页面存在，缺少浏览器验收、契约专项和 CI |
| 最终提交 | ❌ 未做 | 工作区有未提交重构 |

## 2. 已验证证据

| 验证 | 结果 | 证据 |
|---|---|---|
| C++ 核心测试 | 全部通过 | `./build/cpp/algoos-core-tests` |
| Python 单元测试 | 39 passed, 1 skipped | `uv run pytest -q` |
| 静态检查 | 通过 | `uv run ruff check python experiments scripts protocol` |
| 集成测试 | 9 passed（真实 MinIO） | [manifest](../../artifacts/verification/20261116T135504Z_minio_integration/manifest.json) |
| 特判 / 取消 / 短路 E2E | PASSED | [manifest](../../artifacts/verification/20261116T135655Z_special_flow/manifest.json) |
| 缓存 / 重启 / 重派 / fallback E2E | PASSED | [manifest](../../artifacts/verification/20261116T135725Z_reliability_flow/manifest.json) |
| 本机基线 smoke | PASSED | [manifest](../../artifacts/verification/20261116T082019Z_baseline_smoke/manifest.json) |
| 真实集群实验 | 6/6 判定一致 | [manifest](../../artifacts/experiments/20261116T140112Z_cluster_cache_aware/manifest.json) |

## 3. 本机 S3 口径

- 默认：真实 MinIO 容器 `cgr.dev/chainguard/minio@sha256:9158c67a...`，
  AGPLv3 社区版 `RELEASE.2026-11-12T01-15-23Z`，由 Chainguard 构建。
- 原因：上游 `minio/minio` 镜像在当前环境被 registry 拒绝；`dl.min.io` 社区二进制
  返回 410；`aistor` 二进制无 license 会拒绝所有 S3 操作。
- 回退：`ALGOOS_S3_MODE=moto` / `ALGOOS_TEST_S3=moto`。moto 只能写“S3 兼容测试”，
  不能写“MinIO 验证”。
- 边界：本机单容器，不是生产 MinIO 集群、持久化、TLS 或跨机验证。

## 4. 本轮结构整理

- 删除 `legacy/single_node/`：旧单机入口、旧协议、旧测试、旧 Web 全部移除；需要时从
  Git 历史 `e21772f` 之前的提交取回。
- 删除历史 `artifacts/system/` 杂项，只保留当前通过的 verification 和 experiments 证据。
- 删除 `experiments/reports/`、`system_list/`、空 `.gitkeep`、缓存和运行时 `work/`。
- 旧单机文档移入 `docs/archive/legacy_single_node/`；课程过程文档移入
  `docs/archive/coursework/`。
- 文档按 `requirements/design/operations/status/development/records/reports/archive` 分层。
- 代码文件统一命名；详见 文件结构与命名规范。

## 5. 未完成 / 不能声称

1. KVM 参考多节点未执行，`deploy/kvm/provision.sh` 只是部署资产。
2. 生产沙箱未验证：真实 cgroup OOM、seccomp 逐 syscall、跨机 namespace/时钟。
3. 本机 MinIO 是单容器，不是生产对象存储。
4. 正式实验矩阵未做：需要至少 3 次重复、固定环境、p50/p95/方差和图表。
5. Web 浏览器交互/截图、完整 API 契约、CI 未补。
6. 尚未 final commit/push。
