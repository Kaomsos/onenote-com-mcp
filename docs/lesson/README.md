# 工程经验索引

本目录记录从真实实现、排障和人工验证中提炼出的可复用经验。它解释“为什么会这样”和“哪些假设不可靠”，但不定义当前公开契约或执行流程。

当前契约以 [`../design/`](../design/) 为准，验证流程以 [`../dev/`](../dev/) 和 [`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md) 为准。新增或修改 Lesson 时必须遵循 [`AGENTS.md`](AGENTS.md)。

## Lessons

| Lesson | 主题 | 证据范围 | Canonical 链接 |
| --- | --- | --- | --- |
| [`copy_content_type_exclusions.md`](copy_content_type_exclusions.md) | 删除 FileAttachment/MeetingInfo 专属验证入口的原因与安全边界 | 2026-08-11 当前环境的 FileAttachment GUI 回读；MeetingInfo 为产品优先级决策 | [`tool_contracts.md`](../design/tool_contracts.md)、[`object_model.md`](../design/object_model.md)、[`004`](../todo/004_interactive_copy_move_content_fidelity_validation.md) |
| [`onenote_com_recycle_bin_visibility.md`](onenote_com_recycle_bin_visibility.md) | OneNote COM 回收站可见性不能作为非永久删除的必要验收条件 | 2026-08-09 隔离 Page Move 人工观察与对应合同回归 | [`tool_contracts.md`](../design/tool_contracts.md)、[`isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md) |
