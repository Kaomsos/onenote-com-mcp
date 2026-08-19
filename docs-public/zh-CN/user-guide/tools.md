# 工具总览

[English](../../en/user-guide/tools.md) | [文档首页](../../README.zh-CN.md)

生产 profile 恰好公开 **53 个工具**，按用户任务组织。参数、scope、响应、效果、预算和授权的权威契约是 [tool contracts](../../../docs/design/tool_contracts.md)；本页是导览图。

## 目录

| 类别 | 工具 | 所需门 |
| --- | --- | --- |
| Session | `health_check`、`launch_onenote_gui` | — / UI Control |
| Hierarchy Browse | `list_notebooks`、`get_hierarchy_path`、`expand_notebook`、`expand_section_group`、`expand_section`、`expand_page`、`expand_hierarchy` | — |
| Metadata Get | `get_notebook_metadata`、`get_section_group_metadata`、`get_section_metadata`、`get_page_metadata` | — |
| Query & Search | `query_notebook`、`query_section_group`、`query_section`、`query_page`、`search_pages` | — |
| Page Content Read | `get_page_text`、`get_page_content_objects`、`get_page_content_object_binary` | — |
| Hyperlink | `get_hyperlink` | — |
| Create | `create_notebook`、`create_section_group`、`create_section`、`create_page` | Create（Page 还需 Writes） |
| Rename | `rename_page`、`rename_section_group`、`rename_section` | Writes |
| Reorder | `reorder_page`、`reorder_section`、`sort_children` | Writes |
| Organize | `reparent_page`、`reparent_section`、`reparent_section_group` | Writes + Organize |
| Page Content Mutation | `append_page_content`、`add_page_image_from_file`、`replace_page_body`、`delete_page_content_object` | Writes（替换/删除还需 Deletes；图片还需 Local File IO） |
| Recoverable Delete | `delete_page`、`delete_section`、`delete_section_group` | Deletes |
| Copy | `copy_page`、`copy_section`、`copy_section_group`、`copy_notebook` | Create + Writes |
| Reconstructive Move | `move_page`、`move_section`、`move_section_group` | Create + Writes + Deletes |
| Export | `export_object_to_pdf` | Local File IO |
| UI Navigation | `navigate_to` | UI Control |
| Notebook Lifecycle | `request_notebook_sync`、`close_notebook` | Notebook Lifecycle |

重命名过的工具没有兼容别名，也没有任何环境开关能暴露额外的内部能力。

## 响应 envelope

每个公开调用精确返回以下两种形状之一：

```json
{"ok":true,"result":{},"warnings":[],"execution":{}}
```

```json
{"ok":false,"error":{"code":"...","message":"...","details":{}},"execution":{}}
```

## 值得了解的行为

- **先调用 `health_check`。** 每个会话开始时调用它，它绝不启动 OneNote。每个已授权 effect 都独立要求已存在可见的 OneNote GUI；纯读取不需要。
- **Query 与 Search 的区别。** Query 工具读取层级元数据；`search_pages` 使用 OneNote 的实时索引发现 Page 正文。两者都只返回候选——mutation 仍需精确 ID。
- **类型化 Expand 参数**按对象类型命名（`notebook_id`、`section_group_id`、`section_id`、`page_id`）。
- **`get_page_text`** 默认返回有界的 `sanitized_html_v1`，保留经过审查的格式、链接、列表、标签、表格和规范 Presentation MathML，不暴露 raw Page XML 或二进制 payload。需要旧版 `{text, chars}` 投影时传 `mode="plain"`。
- **Page 编辑按操作提供，不按修订提供。** 公开写入面支持追加、替换整个正文、按精确内容对象 ID 删除和添加图片；不提供修订历史、track changes、按 revision ID 回退或任意文本范围 patch。`expected_modified` 是乐观并发确认字段，不是修订选择器。
- **Page 范围**由布尔 `include_subpages`（默认 `false`）控制：`false` 只选目标 Page，并在需要时保护/提升被排除的后代；`true` 选择完整缩进子树。
- **有界批量。** `create_*`、`rename_*`、`reparent_*` 和可恢复 `delete_*` 接受原单项字段或有界 `items` 批量（1–20）。批量作为整体预检，按输入顺序执行，在第一个失败或不确定的 item 处停止，绝不做大范围回滚或重放。部分结果保留 `applied` / `failed` / `not_attempted` 状态。
- **`sort_children`** 按 `name`、`created` 或 `modified` 对已确认父级的完整直接子序列做稳定排序。子类型由父类型推断：Notebook/SectionGroup 排序 Section；Section/Page 排序 Page。分级 Page 作为完整缩进块移动；SectionGroup 永不参与排序。
- **Copy 和 Move 都是单次重建式调用。** Planning 留在操作内部。Page Copy/Move 精确保留逻辑 Page 标题——包括 `/`、`\`、`:`、重复空格、`%`、`~` 和 Unicode——除非显式传入 `destination_title`。Copy 结果包含逐 Page 验证阶段和类型化失败分类。source revision/authorship marker 以及原始创建/修改时间不会被继承；OneNote 可以生成目标自己的值。`verified`、`lossless` 和 `copy_contract_satisfied` 不承诺这些元数据保真。
- **Move 是重建式的**：先验证 Copy，再非永久删除源。它会产生新 ID，且可能返回不得盲目重试的部分或不确定结果。
- **`export_object_to_pdf`** 只写新 PDF，绝不覆盖已存在的路径。
- **`request_notebook_sync`** 证明请求被接受，不证明同步已完成。
- **SectionGroup 排序不受支持**：观察到的后端只暴露固定名称顺序，而非稳定可变的兄弟顺序。
