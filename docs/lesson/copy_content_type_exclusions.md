# Copy 内容类型排除决策：FileAttachment、MeetingInfo 与 Embedded Spreadsheet

> 状态：当前有效的工程决策
> 首次记录日期：2026-08-11；范围更新：2026-08-12
> 范围：Windows OneNote Desktop、本地 COM、交互式 Copy/Move fixture 取证
> 当前 Copy/Move 契约：[`../design/tool_contracts.md`](../design/tool_contracts.md)
> 当前对象模型：[`../design/object_model.md`](../design/object_model.md)
> 工作跟踪：[`../todo/004_interactive_copy_move_content_fidelity_validation.md`](../todo/004_interactive_copy_move_content_fidelity_validation.md)

## 决策

不为 `FileAttachment`、`MeetingInfo` 或 `Embedded Spreadsheet`（内嵌电子表格）提供专属 bootstrap、Recipe、Scenario、dry-run case 或 comparator 合同测试。它们也不属于 TODO 004 的完成条件。

该排除只缩小取证范围，不改变生产安全合同：三类内容仍是 unverified/unsupported；Copy 不能据此满足共享 Copy 合同，Move 因而也不能继续删除源。`Embedded Spreadsheet` 在本文中只是 OneNote 产品能力类别，不代表已经观察到同名的公开 `PageContentObject.kind` 或 XML 节点。

## 为什么排除 FileAttachment

公开 Page 对象模型的类型字段是 `kind`；parser 内部的 `type` 不是 detector 或 comparator 的公开输入。2026-08-11，在下述单一环境中，用户连续四次通过 OneNote GUI 的“插入 → 文件附件”添加不同文件，回读结果均为 `kind=InsertedFile`，没有观察到独立 `kind=FileAttachment`。其中三次有确认后的机器 snapshot，另一次为用户报告；更早的一次拖放也回读为 `InsertedFile`。

因此，当前 GUI 没有可重复生成独立 FileAttachment fixture 的方法。继续保留专属入口只会制造一个用户无法满足的测试合同，没有实际取证价值。这个结论不表示 `FileAttachment` 与 `InsertedFile` 等价，也不表示其他 OneNote 版本永远不会产生 `FileAttachment`；生产 parser 仍应区分两个 kind 并 fail closed。

## 为什么排除 MeetingInfo

`MeetingInfo` 内容使用场景小众，GUI 生成与构造 synthetic、无敏感信息的稳定 fixture 都较困难，而当前 Copy/Move 取证价值较低。它因此按产品优先级排除，不是基于已经证明的 COM 保真结论。

如果生产回读遇到 `MeetingInfo`，仍应将其视为 unverified 内容，不能因没有专属测试入口而静默放行。

## 为什么排除 Embedded Spreadsheet

OneNote 的“内嵌电子表格”尚未收集真实 Page 对象模型、Page XML、二进制引用或 Copy read-back 证据。当前不能断言它公开为哪个 `kind`，也不能假设它等同于 `Table`、`InsertedFile`、`FileAttachment` 或某个 Office/OLE 节点。

项目因此明确把 `Embedded Spreadsheet` 记为当前不支持的产品能力类别：它不进入 validated/lossless 集合，也没有专属 detector/comparator。实际回读若出现未知或未验证节点，现有 fail-closed Copy 合同必须拒绝；Move 只复用该 Copy 合同，不另设允许它通过的类别门禁。

## 观察环境与证据边界

| 项目 | 观察值 |
| --- | --- |
| OneNote / Office version | `16.0.20228.20158` |
| Office platform | `x64` |
| Windows | Windows 10 Pro，display version `25H2`，build `26200.8875` |
| Culture / time zone | `zh-CN` / `China Standard Time` |

FileAttachment 的结论只适用于上述环境与已执行的 GUI 路径。MeetingInfo 与 Embedded Spreadsheet 的结论都是当前产品范围决定；项目尚未对内嵌电子表格执行真实 backend run，因此不得把“不支持”误写成该 OneNote 版本的 COM 保真失败或跨版本平台限制。本文不保存 Page 正文、附件名称、Notebook 名称、对象 ID、用户路径或二进制内容。

## 重新纳入的条件

只有在项目重新评估价值并明确变更范围后，才能恢复任一专属入口。FileAttachment 还必须先在一个记录了环境信息的真实 OneNote 运行中观察到独立 `kind=FileAttachment`；MeetingInfo 必须先定义可重复、无敏感信息的 GUI 生成步骤和自动 comparator；Embedded Spreadsheet 必须先通过同样记录环境的 discovery run 确认公开 `kind`、content-free XML projection、外部/二进制引用边界和可重复 GUI 生成步骤，再定义机器 comparator。任何恢复都必须重新经过 TODO、静态安全门、纯合同测试和用户执行的真实场景评审。
