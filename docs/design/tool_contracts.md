# 公开 MCP Tool 契约

> 状态：当前实现态
> 更新日期：2026-08-15
> 权威来源：`src/local_onenote_mcp/tool_surface.py`、canonical Operation Registry 与公开 Tool schema

生产 MCP 只有一个 User profile，按用户任务固定公开 **52 个 Tool**。Tool 是否可见、是否获准执行、实现成熟度是三个独立维度：`tools/list` 只返回本页目录；所有 effect gate 默认关闭；内部能力不能通过环境变量重新注册。

## 1. 统一响应

成功 envelope 的顶层键精确为：

```json
{"ok":true,"result":{},"warnings":[],"execution":{}}
```

失败 envelope 的顶层键精确为：

```json
{"ok":false,"error":{"code":"stable_typed_code","message":"content-free summary","details":{}},"execution":{}}
```

业务字段只在 `result` 内。`execution` 是 content-free Operation Runtime 投影，记录 operation、stage、effect kind、backend category、attempt/replay、backend-call count、retry safety 与 cache generation；它不包含请求参数、对象 ID、用户路径、Page 内容、raw XML、binary 或 secret。失败不会同时返回伪成功 `result`。

## 2. 52 个公开 Tool

签名中的 `null` 表示可选值缺省。调用者不得用空字符串代替 optional value。所有对象参数均为精确 OneNote COM ID；名称、路径和 Query 结果只能用于发现候选，mutation 前必须固定 ID 并提交确认字段。

### Session（2）

| Tool | 参数 | 合同 |
| --- | --- | --- |
| `health_check` | 无 | check-only；验证现有 `ONENOTE.EXE` 进程和可见窗口，绝不启动 GUI。成功时返回 52 项分类计数、7 个公开授权状态及运行时预算。 |
| `launch_onenote_gui` | 无 | UI Control；已就绪时不启动。进程完全不存在时只解析受信任的注册目标并发出一次启动请求，再有界观察 GUI readiness。process-only、解析失败、启动异常与超时均 typed fail closed。 |

### Hierarchy Browse（7）

| Tool | 参数 |
| --- | --- |
| `list_notebooks` | 无；列出当前打开 Notebook。 |
| `get_hierarchy_path` | `object_id` |
| `expand_notebook` | `notebook_id`；递归到 Section 叶节点。 |
| `expand_section_group` | `section_group_id`；递归到 Section 叶节点。 |
| `expand_section` | `section_id`；返回完整 Page 缩进树。 |
| `expand_page` | `page_id`；返回该 Page 的完整缩进后代。 |
| `expand_hierarchy` | `root_id, max_depth=8, include_recycle_bin=false`；数值深度边界。 |

所有 Expand 返回统一递归 `tree={item,children[]}`，只读取 hierarchy metadata；target Notebook 内关系不完整、重复、循环、跨 Section 或超过公共响应预算时明确失败。

### Metadata Get（4）

`get_notebook_metadata(notebook_id)`、`get_section_group_metadata(section_group_id)`、`get_section_metadata(section_id)`、`get_page_metadata(page_id)` 都只读取一个已知 exact-ID 对象的稳定元数据；不按名称选择、不读取 Page 正文。

### Query & Search（5）

| Tool | 参数与边界 |
| --- | --- |
| `query_notebook` | nullable `name_equals/name_contains/modified_after/modified_before`，`offset=0, page_size=200`。固定查询全部当前打开 Notebook。 |
| `query_section_group` | 必需 `scope`，nullable 名称、`parent_id`、modified 过滤，`include_recycle_bin=false, offset=0, page_size=200`。 |
| `query_section` | 与 SectionGroup Query 同类，`parent_id` 必须是 direct parent。 |
| `query_page` | 必需 `scope`，nullable `title_equals/title_contains/section_id/parent_page_id/modified_*`；不读取正文。 |
| `search_pages` | `query, scope, offset=0, page_size=200, include_snippets=true, include_recycle_bin=false`；使用 OneNote live index，没有本地全量扫描 fallback。 |

`scope` 是 discriminated union：`{"mode":"root"}` 或 `{"mode":"start_node","start_node_id":"..."}`。分页发生在过滤之后，不缩小底层 hierarchy/index 检索。Query 与 Search 返回候选，不可直接成为 mutation 的名称选择器。

### Page Content Read（3）与 Hyperlink（1）

| Tool | 参数与边界 |
| --- | --- |
| `get_page_text` | `page_id, max_chars=60000`；受硬字符预算约束。 |
| `list_page_content_objects` | `page_id`；返回 typed `PageContentObject` 清单以及可用的稳定删除/二进制读取 ID。 |
| `get_page_object_binary` | `page_id, page_content_object_id`；校验对象归属并受硬 binary 响应预算约束。 |
| `get_hyperlink` | `object_id, page_content_object_id=null, link_type="desktop"|"web"`。 |

Raw Page XML 不属于公开读取降级路线。

### Create（4）

均需要 Writes。

| Tool | 参数 |
| --- | --- |
| `create_notebook` | `name, base_folder=null` |
| `create_section_group` | `parent_id, name` |
| `create_section` | `parent_id, name` |
| `create_page` | `section_id, title, content="", content_format="plain"` |

`content_format` 支持当前实现验证的 plain/HTML/Markdown 路径。Create 只接受精确 parent ID，并通过 allocated ID 和 live read-back 收敛；重复标题不会选择旧 Page。

### Rename（3）、Reorder（2）、Organize（3）

Rename 与 Reorder 需要 Writes：

- `rename_page(page_id, title, expected_title, expected_section_id, expected_modified=null)`
- `rename_section_group(section_group_id, new_name, expected_name, expected_parent_id, expected_modified=null)`
- `rename_section(section_id, new_name, expected_name, expected_parent_id, expected_modified=null)`
- `reorder_page(page_id, expected_title, expected_section_id, after_page_id=null, page_level=0, expected_modified=null)`
- `reorder_section(section_id, expected_name, expected_parent_id, after_section_id=null, expected_modified=null)`

Organize 同时需要 Writes + Organize：

- `reparent_page(page_id, destination_section_id, expected_title, expected_section_id, expected_modified=null, page_scope="page_only")`
- `reparent_section(section_id, destination_parent_id, expected_name, expected_parent_id, expected_modified=null)`
- `reparent_section_group(section_group_id, destination_parent_id, expected_name, expected_parent_id, expected_modified=null)`

`page_scope` 只能是 `page_only | indentation_subtree`。Reparent 只在同一 Notebook 内改变父级；Section 与 SectionGroup 保持对象 ID，Page 在 OneNote 重映射时返回经验证的一对一 `id_map` 和最终对象。它不是 Copy 或跨 Notebook Move。SectionGroup reorder 没有稳定后端语义，因此不公开。

### Page Content Mutation（4）

| Tool | 参数 | 授权 |
| --- | --- | --- |
| `append_page_content` | `page_id, content, expected_title, expected_section_id, expected_modified=null, content_format="plain", x=null, y=null` | Writes |
| `add_page_image_from_file` | `page_id, image_path, expected_title, expected_section_id, expected_modified=null, x=36, y=120, width=null, height=null` | Writes + Local File IO |
| `replace_page_body` | `page_id, content, expected_title, expected_section_id, expected_modified=null, content_format="plain"` | Writes + Deletes |
| `delete_page_content_object` | `page_id, page_content_object_id, expected_title, expected_section_id, expected_modified=null` | Deletes |

图片格式从文件 magic bytes 推断，并与扩展名交叉验证；调用者不能指定 `image_format`。`replace_page_body` 不修改 title，是可能返回 partial/reconciliation 的多步 saga。内容对象删除 ID 必须来自同一 Page 的 typed object list，并且该对象被标记为可删除。

### Recoverable Delete（3）

`delete_page`、`delete_section`、`delete_section_group` 均需要 Deletes 和 exact-ID confirmation，并固定执行非永久、可恢复删除。公开 schema 没有 `permanently`；永久删除工具当前不存在。

### Copy（4）与 Reconstructive Move（3）

Copy 需要 Writes + Copy；Move 需要 Writes + Copy + Deletes。

- `copy_page(page_id, destination_section_id, expected_title, expected_section_id, expected_modified=null, destination_title=null, page_scope="page_only")`
- `copy_section(section_id, destination_parent_id, expected_name, expected_parent_id, expected_modified=null, destination_name=null)`
- `copy_section_group(section_group_id, destination_parent_id, expected_name, expected_parent_id, expected_modified=null, destination_name=null)`
- `copy_notebook(notebook_id, expected_name, expected_modified=null, destination_name=null, destination_base_folder=null)`
- `move_page(...)` 与 Page Copy 参数同构
- `move_section(...)` 与 Section Copy 参数同构
- `move_section_group(...)` 与 SectionGroup Copy 参数同构

七个操作都是单次调用：Runtime 内部从 live source/destination 建立计划、执行预算检查、复制、验证并返回新 ID 映射；不接受 `plan_digest` 或 planning token。Move 只有在 Copy 已验证且源状态重验通过后才执行源对象的非永久删除；partial/indeterminate 不自动重放。Page 默认为单页，容器始终递归。

### Export（1）、UI Navigation（1）、Notebook Lifecycle（2）

| Tool | 参数 | 授权与完成语义 |
| --- | --- | --- |
| `export_object_to_pdf` | `object_id, target_path` | Local File IO；只写新 PDF，不接受 `format` 或 `overwrite`，目标已存在即失败。 |
| `navigate_to` | `object_id, page_content_object_id=null, new_window=false` | UI Control；只报告 UI action acceptance。 |
| `request_notebook_sync` | `notebook_id` | Notebook Lifecycle；只证明请求被接受，不证明同步完成。 |
| `close_notebook` | `notebook_id, expected_name, expected_modified=null` | Notebook Lifecycle；通过 live open-state 收敛。 |

## 3. 默认授权模型

七个用户可配置 gate 默认均为 false：

| 授权 | 环境变量 | 直接覆盖 |
| --- | --- | --- |
| Writes | `LOCAL_ONENOTE_ENABLE_WRITES` | Create、Rename、Reorder、Append 及组合操作的写阶段 |
| Deletes | `LOCAL_ONENOTE_ENABLE_DELETES` | 内容对象删除、可恢复删除、Replace 删除阶段、Move 源删除 |
| Organize | `LOCAL_ONENOTE_ENABLE_ORGANIZE` | `reparent_*`，并要求 Writes |
| Copy | `LOCAL_ONENOTE_ENABLE_COPY` | `copy_*`，并要求 Writes；Move 还要求 Deletes |
| Local File IO | `LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO` | 从文件加图、导出 PDF |
| UI Control | `LOCAL_ONENOTE_ENABLE_UI_CONTROL` | GUI launch、typed navigation |
| Notebook Lifecycle | `LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE` | Sync request、Close |

授权在 Operation Runtime 的 authorizer 阶段、取得协调 lease 和调用 handler 之前执行；Service 再做纵深门控。旧 experimental/move 环境变量不再授予任何能力。内部 `Permanent Deletes` 与 `Raw XML` 防线不属于公开 52 项授权面，也不创建 Tool。

## 4. 非公开能力

`resolve_identifier`、`get_page_xml`、`navigate_to_url`、`get_special_locations`、`get_parent` 保留在非注册 Internal & Incubating catalog；它们与 User profile 不相交，也没有批量 exposure 开关。`reorder_section_group`、任意 raw hierarchy/page mutation、generic open/find/merge/filing、所有 Plan/Preview Tool 均禁止进入生产 MCP。

完整机器投影与 promotion requirements 见 `src/local_onenote_mcp/tool_surface.py`。对象语义见 [对象模型](object_model.md)，执行阶段与审计见 [Operation Runtime](operation_runtime.md)，低层边界见 [内部低层与诊断操作](advanced_operations.md)。
