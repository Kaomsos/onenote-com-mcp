# 工程经验索引

本目录记录从真实实现、排障和人工验证中提炼出的可复用经验。它解释“为什么会这样”和“哪些假设不可靠”，但不定义当前公开契约或执行流程。

当前契约以 [`../design/`](../design/) 为准，验证流程以 [`../dev/`](../dev/) 和 [`../../tests/manual_validation/README.md`](../../tests/manual_validation/README.md) 为准。新增或修改 Lesson 时必须遵循 [`AGENTS.md`](AGENTS.md)。

## Lessons

| Lesson | 主题 | 证据范围 | Canonical 链接 |
| --- | --- | --- | --- |
| [`copy_content_type_exclusions.md`](copy_content_type_exclusions.md) | 删除 FileAttachment/MeetingInfo 专属验证入口的原因与安全边界 | 2026-08-11 当前环境的 FileAttachment GUI 回读；MeetingInfo 为产品优先级决策 | [`tool_contracts.md`](../design/tool_contracts.md)、[`object_model.md`](../design/object_model.md)、[`004`](../todo/004_interactive_copy_move_content_fidelity_validation.md) |
| [`fixture_cache_consumer_materialization_and_live_validation.md`](fixture_cache_consumer_materialization_and_live_validation.md) | Cache consumer 的 live hierarchy 激活、ID 重绑定、失败归因、物理路径命名与 working lease 生命周期 | 2026-08-11 InsertedFile 排障/consumer 证据，以及双 Notebook 同 entry 并发隔离、真实 ID 冲突与恢复证据 | [`architecture.md`](../design/architecture.md)、[`isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md)、[`manual_validation/README.md`](../../tests/manual_validation/README.md) |
| [`onenote_page_object_kind_and_file_attachment_representation.md`](onenote_page_object_kind_and_file_attachment_representation.md) | 公开 `kind` 合同、文件附件表示边界，以及删除 FileAttachment/MeetingInfo 专属验证入口的排除决策 | 2026-08-11 三次菜单操作机器回读、一次额外用户观察与一次独立拖放机器回读；MeetingInfo 为产品优先级决策 | [`object_model.md`](../design/object_model.md)、[`tool_contracts.md`](../design/tool_contracts.md)、[`isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md) |
| [`onenote_com_recycle_bin_visibility.md`](onenote_com_recycle_bin_visibility.md) | OneNote COM 回收站可见性不能作为非永久删除的必要验收条件 | 2026-08-09 隔离 Page Move 人工观察与对应合同回归 | [`tool_contracts.md`](../design/tool_contracts.md)、[`isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md) |
| [`onenote_mutation_created_target_identity.md`](onenote_mutation_created_target_identity.md) | 重名 Page 下 allocated ID、friendly path 与 mutation target 的身份边界 | 2026-08-11 隔离 Page Copy 身份失败/修复对照、v4 selection-placeholder 诊断与最终六 case/cleanup/close 闭环 | [`tool_contracts.md`](../design/tool_contracts.md)、[`object_model.md`](../design/object_model.md)、[`isolated_mutation_validation.md`](../dev/isolated_mutation_validation.md) |
