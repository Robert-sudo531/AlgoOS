# 文档索引

按“当前使用”和“历史归档”分层。新增文档按
[文件结构与命名规范](development/file_structure.md) 放置。

## 当前文档

| 区域 | 入口 | 用途 |
|---|---|---|
| 部署自测 | [../deployment.md](../deployment.md) | 本机、systemd、KVM 部署与自测 |
| 需求 | [requirements/](requirements/) | 实施总案、目标 UML/接口 |
| 设计 | [design/distributed_architecture.md](design/distributed_architecture.md) | 当前分布式架构与边界 |
| 运维 | [operations/cluster_deployment.md](operations/cluster_deployment.md) | 本机、systemd、cloud-init、KVM 部署 |
| 状态 | [status/implementation_status.md](status/implementation_status.md) | 当前实现状态、证据、未完成项 |
| 交接 | [status/handoff.md](status/handoff.md) | 接手入口、快速验收、常见坑 |
| 规范 | [development/file_structure.md](development/file_structure.md) | 文件树、命名、证据、删除规则 |
| 记录 | [records/progress.md](records/progress.md) | 按日期追加的开发记录 |

## 报告

- [reports/midterm_report.md](reports/midterm_report.md)
- [reports/slides_advice_2026-11-04.md](reports/slides_advice_2026-11-04.md)
- [reports/midterm_progress_2026-11-04.pptx](reports/midterm_progress_2026-11-04.pptx)
- [reports/templates/course_report_template.docx](reports/templates/course_report_template.docx)

## 归档

- [archive/legacy_single_node/](archive/legacy_single_node/)：已删除的旧单机实现的文档；
- [archive/coursework/](archive/coursework/)：阶段任务、旧计划、协作记录；
- [archive/2026-11-03_cleanup.md](archive/2026-11-03_cleanup.md)：一次历史清理记录。

归档只读，不再更新；当前实现和结构以 `README.md`、`docs/status/`、
`docs/development/` 为准。
