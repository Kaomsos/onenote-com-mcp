# 产品能力边界

本目录记录产品层已经决定的能力与非承诺。精确参数、响应结构和安全门限仍以 [`../design/tool_contracts.md`](../design/tool_contracts.md) 为权威契约。

## Page 修订与 Copy/Move 元数据

当前产品不提供 OneNote 修订历史、track-changes、按 revision ID 回退或任意文本范围 patch。Page 内容写入只提供公开的有界操作：追加内容、替换整个正文、按精确内容对象 ID 删除，以及从本地文件添加图片。`expected_modified` 只是 mutation 前的乐观并发确认字段，不是修订选择器，也不能恢复历史版本。

Copy 与 Move 是重建式操作。它们为目标创建新对象和新 ID，并在公开支持的标题、正文、内容对象、拓扑投影，以及范围内 Page 根 `dateTime` 的同一 UTC 秒内做验证。Page 转换不会把 source 的 authorship/revision marker 或 `creationTime` / `lastModifiedTime` 传给目标；容器目标同样使用重新创建后的时间元数据。OneNote 可以在写入后生成目标自己的 marker 和其他时间值，但这些值不代表 source 的原始修订身份或时间轴。

因此，Copy/Move 返回的 `verified`、`lossless` 或 `copy_contract_satisfied` 只表示已声明的受支持投影以及范围内全部 Page 的 `page_results[*].date_time.status=verified` 通过，不表示以下信息得到保真：

- source revision marker、作者/最后修改者解析标识或修订历史；
- source 原始创建时间与修改时间；
- 依赖旧对象 ID 的外部入站链接或审计身份。

`date_time.status` 只报告执行期结果（`verified` / `write_failed` / `readback_mismatch` / `source_drifted`），公开响应不含原始或规范化时间。范围内任一 Page 在 planning 时缺失或非法 `dateTime` 会在首个 mutation 前 fail closed。[TODO 043](../todo/043_copy_move_source_timestamp_fidelity.md) 已完成：用户确认 Copy/Move 单 Page 与子树正向真实复验，以及跨秒 Move 返回 `copy_only`、保留 source 并零删除的负向真实证据；该合同不扩展到其他时间字段或容器元数据。
