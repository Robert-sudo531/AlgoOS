# Verification evidence

Each directory is one completed local verification run and must contain `manifest.json`.

Naming:

```text
YYYYMMDDTHHMMSSZ_<flow>
```

Flows:

- `baseline_smoke`：本机服务、迁移/题包、六种判定、双节点；
- `special_flow`：特判、用户取消、有序短路；
- `reliability_flow`：缓存、重复结果、Agent 重启、节点失联重派、特判 fallback；
- `minio_integration`：真实 MinIO + pytest 集成测试。

Rules:

- Only passing runs are kept.
- A run directory is immutable after it is referenced by a document.
- Do not keep failed runs or random temporary names; delete them after diagnosis.
- These are local multi-process results, not production or cross-machine validation.
