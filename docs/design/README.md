# 设计文档索引

本目录保存当前架构、公开契约以及已经定稿但仍由 TODO 跟踪实施的目标设计。尚未实现的设计必须在文档开头显式标注，不能表述为当前行为。

| 文档 | 职责 | 状态 |
| --- | --- | --- |
| [`architecture.md`](architecture.md) | 生产 MCP、bridge、service 与运行时总体架构 | 当前实现态 |
| [`manual_validation_scenario_fixture_architecture.md`](manual_validation_scenario_fixture_architecture.md) | 测试专用 Scenario、Fixture Recipe、cache、working lifecycle 与证据流架构 | 当前实现态 |
| [`object_model.md`](object_model.md) | Notebook、SectionGroup、Section、Page 与内容对象模型 | 当前契约 |
| [`hierarchy_parser.md`](hierarchy_parser.md) | OneNote hierarchy XML 解析边界 | 当前契约 |
| [`tool_contracts.md`](tool_contracts.md) | MCP tool 参数、返回结构、policy 与错误语义 | 当前契约 |
| [`mutation_readiness_and_call_design.md`](mutation_readiness_and_call_design.md) | OneNote mutation readiness 不可预先观测的状态模型、调用顺序、reconciliation 与 lifecycle 边界 | 当前实现合同；bounded-attempt 加固已由 TODO 029 完成，operation-wide Runtime 由 TODO 036 承接 |
| [`advanced_operations.md`](advanced_operations.md) | 高级/实验操作与启用边界 | 当前契约 |
| [`windows_fixture_cache_path_budget.md`](windows_fixture_cache_path_budget.md) | Windows fixture cache、staging、materialization 与 working copy 路径配额 | 当前实现合同；验证证据由 TODO 021 跟踪 |
