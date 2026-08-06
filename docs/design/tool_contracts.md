# MCP 工具参数与返回格式（P0/P1 + P2 实验实现）

> 状态：默认工具 profile 的权威契约  
> 更新日期：2026-08-05
> ID 参数均指 OneNote COM 对象 ID，除 `resolve_identifier` 和兼容只读 `list_hierarchy.start_identifier` 外不接受名称或路径。

默认 profile 共 50 个工具；参数和返回格式由 `tools/` 薄适配层公开，业务语义与回读验证由 `services/` 实现。P2 Copy/重建式 Move 已注册但由默认关闭的独立策略保护，尚不能视为真实 OneNote 已验证能力。

## 1. 通用返回 envelope

成功：

```json
{
  "ok": true,
  "complete": true,
  "warnings": [],
  "item": {}
}
```

失败：

```json
{
  "ok": false,
  "complete": false,
  "code": "validation_error | policy_disabled | backend_error | operation_failed | partial_failure",
  "error": "safe message"
}
```

列表通常返回 `items/notebooks/sections/pages`、`count`；对象读取返回 `item`。`partial_failure` 还可返回 `partial=true`、`completed_steps`。底层 COM XML、本机路径或 HRESULT 不进入普通错误。

## 2. 发现、List/Get、Query

| 工具 | 参数 | 成功时的主要返回 |
| --- | --- | --- |
| `health_check` | 无 | 运行时位置、统计、`mutation_policy`、`search_budget`、`copy_budget`。 |
| `resolve_identifier` | `identifier`, `item_type=""` | `item`、`identifier_resolution_order`；仅只读辅助。 |
| `list_notebooks` | `include_recycle_bin=false` | `notebooks`, `count`。 |
| `get_notebook` | `notebook_id` | `item: Notebook`。 |
| `list_section_groups` | `parent_id=""`, `recursive=true`, `include_recycle_bin=false` | `items: SectionGroup[]`, `count`。 |
| `get_section_group` | `section_group_id` | `item: SectionGroup`。 |
| `list_sections` | `parent_id=""`, `recursive=true`, `include_recycle_bin=false` | `sections`, `count`；`recursive=false` 只返回直属项。 |
| `get_section` | `section_id` | `item: Section`。 |
| `list_pages` | `section_id`, `include_recycle_bin=false` | `section`, `pages`, `count`；不读取正文。 |
| `get_page` | `page_id` | `item: Page`；不读取正文。 |
| `query_hierarchy` | `resource_type`, `name_equals=""`, `name_contains=""`, `parent_id=""`, `modified_after=""`, `modified_before=""`, `include_recycle_bin=false`, `limit=100` | `items`, `count`, `total_matches`, `truncated`。 |
| `get_path` | `object_id` | `item`, `path`, `ancestors`。 |
| `get_parent` | `object_id` | `item`, `parent`, `parent_id`。 |
| `get_tree` | `root_id`, `max_depth=8`, `include_recycle_bin=false` | `tree={item,children[]}`；Page 使用缩进关系。 |
| `list_hierarchy` | `start_identifier=""`, `scope="pages"`, `include_xml=false`, `include_recycle_bin=false` | 稳定字段 `items`, `count`；兼容读取接口。 |
| `get_special_locations` | 无 | `locations={backup,unfiled,default_notebook_folder}`。 |

`resource_type/item_type` 取 `notebook/section_group/section/page`。`scope` 取 `self/children/notebooks/sections/pages`。

## 3. Page 内容与 Search

| 工具 | 参数 | 成功时的主要返回 |
| --- | --- | --- |
| `get_page_text` | `page_id`, `max_chars=60000` | `text`, `chars`；过长内容带截断标记。 |
| `get_page_xml` | `page_id`, `page_info="basic"` | `xml`。`page_info` 见下方枚举。 |
| `get_page_objects` | `page_id` | `objects: PageContentObject[]`, `count`。 |
| `get_binary_content` | `page_id`, `callback_id` | 已复核的 `object`、`base64`。 |
| `search_pages` | `query`, `scope_type`, `scope_id`, `backend="local_scan"`, `max_results=20`, `include_snippets=true`, `include_recycle_bin=false` | `pages`, `count`, `scope`, `search_backend`, `scan_budget`。 |

`scope_type` 只取 `notebook/section_group/section`，不允许空范围。`backend` 只取 `local_scan/onenote_index`；index 失败不会静默回退。本地扫描在读取首个 Page 正文前检查候选页数量，并限制单页字符、总字符和总耗时。

`page_info`：`basic/binary/selection/binary_selection/file_type/binary_file_type/selection_file_type/all`。

## 4. Create 与 Page typed mutation

下列工具均要求 `LOCAL_ONENOTE_ENABLE_WRITES=true`：

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `create_notebook` | `name_or_path`, `base_folder=""` | `notebook_id`, `item`, `path`；按新对象 ID/路径回读。 |
| `create_section_group` | `parent_id`, `group_name` | `section_group_id`, `section_group`, `parent`, `path`。 |
| `create_section` | `parent_id`, `section_name` | `section_id`, `section`, `parent`, `path`。 |
| `create_page` | `section_id`, `title`, `content=""`, `content_format="plain"`, `new_page_style="blank_with_title"` | `page_id`, `page`, `section`, `path`。 |
| `update_page_title` | `page_id`, `title`, `expected_title`, `expected_section_id`, `expected_modified=null` | 更新后的 `item`；验证同 ID 和新标题。 |
| `append_to_page` | `page_id`, `content`, `expected_title`, `expected_section_id`, `expected_modified=null`, `content_format="plain"`, `x=null`, `y=null` | `item`, `appended=true`, `before_modified`；验证内容摘要变化。 |
| `add_image_to_page` | `page_id`, `image_path`, `expected_title`, `expected_section_id`, `expected_modified=null`, `image_format=""`, `x=36`, `y=120`, `width=null`, `height=null` | `item`, `image_path`, 实际 `width/height`；验证内容摘要变化。 |
| `replace_page_body` | `page_id`, `content`, `expected_title`, `expected_section_id`, `expected_modified=null`, `title=null`, `content_format="plain"` | `item`, `deleted_objects`, `replaced`, `partial`；非原子。 |

`content_format` 取 `plain/html/markdown`；`new_page_style` 取 `default/blank_with_title/blank_no_title`。Markdown 富转换依赖可选 OneMore，缺失时按现有转换器边界处理。

## 5. Rename、Reorder 与实验 Move

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `rename_section_group` | `section_group_id`, `new_name`, `expected_name`, `expected_parent_id`, `expected_modified=null` | `item`, `previous_name`；验证同 ID、父级和新名称。 |
| `rename_section` | `section_id`, `new_name`, `expected_name`, `expected_parent_id`, `expected_modified=null` | 同上。 |
| `reorder_page` | `page_id`, `expected_title`, `expected_section_id`, `after_page_id=""`, `page_level=0`, `expected_modified=null` | `item`, `pages`；验证位置与缩进。空 `after_page_id` 表示置顶，`page_level=0` 表示保留。 |
| `move_section` | `section_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `expected_modified=null` | `item`, `verified`；验证 Section ID、Page ID/顺序及忽略层级/时钟属性后的 Page 内容摘要。 |

前 3 个要求写开关。`move_section` 还要求 `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION=true`，且只允许同 Notebook；完成 `docs/dev/isolated_mutation_validation.md` 前必须保持关闭。

## 6. Delete

Delete 总开关为 `LOCAL_ONENOTE_ENABLE_DELETES=true`；`permanently=true` 还要求 `LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES=true`。默认进入 OneNote 回收站。

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `delete_section_group` | `section_group_id`, `expected_name`, `expected_parent_id`, `expected_modified=null`, `permanently=false` | 原 `item`, `final_state`, `deleted`, `permanently`。 |
| `delete_section` | `section_id`, `expected_name`, `expected_parent_id`, `expected_modified=null`, `permanently=false` | 同上。 |
| `delete_page` | `page_id`, `expected_title`, `expected_section_id`, `expected_modified=null`, `permanently=false` | 同上。 |
| `delete_page_content` | `page_id`, `object_id`, `expected_title`, `expected_section_id`, `expected_modified=null` | `page_id`, `object_id`, `deleted`；删除前后复核对象快照。 |

Notebook 没有 Delete 工具。

## 7. P2 Copy 与 Page 重建式 Move（实验）

计划工具只读；执行工具除现有确认字段外必须提交刚生成的 `plan_digest`。摘要包含源树、每页完整 XML hash、目标直属子项、名称和执行模式；执行前重算不一致即在 mutation 前拒绝。计划返回的 `snapshots.source` 公开稳定资源列表与 Page hash、`snapshots.destination` 公开目标直属快照，但不返回原始 Page XML。

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `plan_copy` | `source_id`, `destination_parent_id=""`, `destination_name=""`, `destination_base_folder=""` | `plan_digest`, `source`, `destination`, `estimated`, `copyability`, `steps`, `execute_tool`。 |
| `copy_page` | `page_id`, `destination_section_id`, `expected_title`, `expected_section_id`, `plan_digest`, `expected_modified=null`, `destination_title=""` | 完整缩进子树的 `item`, `copy_report`, `created_ids`；目标根追加为 level 1。 |
| `copy_section` | `section_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `plan_digest`, `expected_modified=null`, `destination_name=""` | 递归创建 Section 与全部 Page。 |
| `copy_section_group` | `section_group_id`, 其余确认/目标字段同上 | 递归创建 SectionGroup、Section 和 Page。 |
| `copy_notebook` | `notebook_id`, `expected_name`, `plan_digest`, `expected_modified=null`, `destination_name=""`, `destination_base_folder=""` | 新 Notebook 及全部后代；不提供 Notebook 删除回滚。 |
| `plan_reconstructive_move_page` | `page_id`, `destination_section_id`, `destination_title=""` | Copy 计划加源 Page 回收步骤和外部入站链接警告。 |
| `reconstructive_move_page` | `page_id`, `destination_section_id`, `expected_title`, `expected_section_id`, `plan_digest`, `expected_modified=null`, `destination_title=""` | lossless/verified 后叶到根回收源 Page；否则 `partial_failure`, `outcome=copy_only`, `source_deleted=false`。 |

`copy_report` 固定包含 `id_map/copied_counts/skipped_content/issues/lossless/verified/page_results`；Notebook Copy 还包含经过创建返回值核对的 `destination_path`。已知有损但执行完整的 Copy 可返回成功和 warning；运行中失败保留已创建目标，返回 `created_ids/completed_steps/failed_step`，不做破坏性自动回滚。

Page Copy 始终包含完整子树。名称冲突按直属子项 case-insensitive 拒绝；没有覆盖、合并或自动后缀。范围内已识别 ID 引用会改写，范围外出站链接保留；重建式 Move 不扫描外部入站链接且始终返回 old→new ID。

递归容器按源 hierarchy snapshot 的确定性深度优先顺序创建。Page 的顺序和相对 `page_level` 必须回读验证；Section/SectionGroup 没有稳定 COM 顺序字段，因此只承诺并验证父子结构，不声明容器顺序保真。Notebook 名称冲突只在解析后的目标 `destination_base_folder/name` 路径上判断，不因其他目录存在同名已打开 Notebook 而误拒绝。

Move 删除阶段的部分失败分别返回 `attempted_source_ids`、已确认带回收站标记的 `recycled_source_ids/deleted_source_ids`、状态未知的 `unverified_source_ids` 和尚未完成步骤的 `remaining_source_ids`；未通过回读验证的 ID 不得计入已回收列表。

当前候选 XML 内容会尽力保留；能力清单除 Outline/Image/附件/墨迹/媒体对象外，还单独识别 `RichText/Table/List/Tag/MeetingInfo`。真实隔离验证尚未确认的类型产生 `content_type_unverified`，使 `lossless=false` 并阻止 Move 删除源。已知顶层内容块内只要出现不在 OneNote 2013 静态节点 allowlist 的后代节点，整个顶层块即省略并返回 `unsupported_nested_page_node`，不会静默透传未来扩展。验证流程见 `tests/manual_validation/README.md`。

## 8. Export、导航、同步与关闭

| 工具 | 参数 | 成功时的主要返回 |
| --- | --- | --- |
| `publish_object` | `object_id`, `target_path`, `format="pdf"`, `overwrite=false` | `item`, `path`, `format`；只支持 Notebook/Section/Page。 |
| `navigate_to` | `object_id`, `page_content_object_id=""`, `new_window=false` | `item`, `navigated=true`。 |
| `navigate_to_url` | `url`, `new_window=false` | `navigated=true`。 |
| `get_hyperlink` | `object_id`, `page_content_object_id=""`, `web=false` | `item`, `hyperlink`。 |
| `sync_notebook` | `notebook_id` | `item`, `synced=true`；不把未验证的子对象 Sync 暴露为稳定能力。 |
| `close_notebook` | `notebook_id`, `expected_name`, `expected_modified=null` | 原 `item`, `final_state`, `closed=true`；要求写开关且不暴露 `force`。 |

导出格式：`one/onepkg/mhtml/mht/pdf/xps/word/doc/docx/emf/html/one2007`。`publish_object` 会写本地文件，但不修改 OneNote 对象。

## 9. 配置与默认 profile

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `LOCAL_ONENOTE_ENABLE_WRITES` | `false` | Create、Update、Rename、Reorder、Close。 |
| `LOCAL_ONENOTE_ENABLE_DELETES` | `false` | 层级和 Page 内容删除。 |
| `LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES` | `false` | 永久删除，不能替代 Delete 总开关。 |
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION` | `false` | 同 Notebook Section Move，不能替代写开关。 |
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY` | `false` | 四层 Copy；不能替代写开关。 |
| `LOCAL_ONENOTE_ENABLE_RECONSTRUCTIVE_MOVE_PAGE` | `false` | Page 重建式 Move；还要求 Writes、Deletes 和 Experimental Copy。 |
| `LOCAL_ONENOTE_ENABLE_RAW_XML` | `false` | 启动时注册开发 profile 工具。 |
| `LOCAL_ONENOTE_MAX_SEARCH_PAGES` | `200` | 本地扫描候选 Page 上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_PAGE_CHARS` | `100000` | 单 Page 扫描字符上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS` | `2000000` | 单次扫描总字符上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_SECONDS` | `30` | 单次本地扫描秒数上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_SNIPPET_CHARS` | `400` | snippet 上限。 |
| `LOCAL_ONENOTE_MAX_COPY_RESOURCES` | `1000` | 单次 Copy 的层级对象上限。 |
| `LOCAL_ONENOTE_MAX_COPY_PAGES` | `200` | 单次 Copy 的 Page 上限。 |
| `LOCAL_ONENOTE_MAX_COPY_CONTENT_OBJECTS` | `10000` | 单次 Copy 的内容对象上限。 |
| `LOCAL_ONENOTE_MAX_COPY_PAGE_XML_BYTES` | `33554432` | 单 Page 完整 XML 字节上限。 |
| `LOCAL_ONENOTE_MAX_COPY_TOTAL_XML_BYTES` | `268435456` | 单次计划全部 Page XML 字节上限。 |
| `LOCAL_ONENOTE_MAX_COPY_PLAN_SECONDS` | `300` | 只读计划阶段秒数上限。 |
| `LOCAL_ONENOTE_MAX_COPY_EXECUTE_SECONDS` | `1800` | 执行阶段秒数上限；超限按部分失败报告。 |

默认不注册 `update_page_xml/update_hierarchy_xml/delete_hierarchy/open_hierarchy/find_meta/merge_sections/set_filing_location`。开发 profile 即使注册 raw mutation，也仍需对应 write/delete 开关；`force` 不进入默认 typed 工具。
