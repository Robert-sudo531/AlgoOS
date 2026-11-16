# Experiment evidence

Each directory is one experiment run and must contain `manifest.json` with
`data_source=cluster|simulation`.

Naming:

```text
YYYYMMDDTHHMMSSZ_<kind>_<strategy>
```

Kinds:

- `cluster_<strategy>`：真实本机多进程集群，`data_source=cluster`；
- `simulation_<profile>`：离散事件模拟，`data_source=simulation`；
- `placement_sa`：模拟退火副本放置，`data_source=simulation`。

Rules:

- `data_source` is the only allowed source label; never present simulation data as cluster data.
- Keep the generated `manifest.json`; cluster runs also keep `runs.csv` and `summary.json`.
- Formal conclusions require at least three repeated runs and reported variance/percentiles.
- Local multi-process results are not KVM or production results.
