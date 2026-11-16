# 实验目录

三块资产严格区分数据来源：

| 目录 | 数据来源 | 说明 |
|---|---|---|
| `runner/` | `cluster` | 通过真实 API 提交固定轨迹，启动本机多进程集群，采集等待/周转/正确性 |
| `simulator/` | `simulation` | 离散事件模拟，固定 seed，不启动服务 |
| `placement/` | `simulation` | 模拟退火副本放置，输出放置表与目标函数曲线数据 |
| `profiles/` | - | 固定策略、负载与 SA 参数 |
| `workloads/` | - | 固定提交轨迹 |
| `analysis/` | - | 读取实验目录，生成 Markdown 报告 |

## 命令

```bash
# 真实本机集群（需要 Docker、g++、MinIO 容器）
PYTHONPATH=python uv run python -m experiments.runner --count 12

# 离线模拟
uv run python -m experiments.simulator.run --profile mixed --count 200 --nodes 2

# SA 放置
uv run python -m experiments.placement.run

# 报告
uv run python -m experiments.analysis.report artifacts/experiments/<run>
```

## 口径

- `manifest.json` 中 `data_source=cluster` 才是真实运行；`simulation` 只能用于策略趋势，不得写成集群性能结论。
- 实验必须固定硬件、编译参数、随机种子、题包与提交轨迹；`manifest.json` 记录数据来源、策略与平台。
- 输出目录统一为 `artifacts/experiments/<UTC>_<kind>_<strategy>/`；每个目录必须有 `manifest.json`。
- 当前运行器只跑 1 次；正式报告需要至少 3 次重复并汇总分位数/方差，重复调度尚未实现。
- 单机多进程验证不等于 KVM 五 VM 或云端验证。
