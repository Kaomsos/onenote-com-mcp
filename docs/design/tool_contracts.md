# MCP 工具参数与返回格式（P0/P1 + P2 实验实现）

> 状态：默认工具 profile 的权威契约  
> 更新日期：2026-08-10
> ID 参数均指 OneNote COM 对象 ID，除 `resolve_identifier` 和兼容只读 `list_hierarchy.start_identifier` 外不接受名称或路径。

默认 profile 共 54 个工具；参数和返回格式由 `tools/` 薄适配层公开，业务语义与回读验证由 `services/` 实现。Section Reorder、三类 Reparent 与 P2 Copy/Move 由默认关闭的独立策略保护；SectionGroup Reorder 不属于受支持能力，任何请求都必须 fail closed。只有下文列入 validated allowlist 的 Page 内容类型具有真实 OneNote 隔离证据，实验工具在对应真实场景完成前不升级稳定性承诺。

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
| `health_check` | 无 | 运行时位置、统计、`mutation_policy`、`search_backends`、`search_scope_types`、`search_budget`、`copy_budget`。 |
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
| `search_pages` | `query`, `scope_type`, `scope_id=""`, `backend="local_scan"`, `max_results=20`, `include_snippets=true`, `include_recycle_bin=false` | `pages`, `count`, `scope`, `search_backend`, `scan_budget`。 |

`scope_type` 取 `all_open_notebooks/notebook/section_group/section`。前三种 typed 对象 scope 必须提交非空且类型匹配的 `scope_id`；`all_open_notebooks` 必须使用默认空 ID，并返回不伪造 COM ID 的合成 `scope={resource_type, notebook_count}`。全局 scope 只覆盖同一次完整 hierarchy 快照中 `is_open` 不为 false 的 Notebook，不扫描已关闭 Notebook、备份目录或 `.one` 文件。

`backend` 只取 `local_scan/onenote_index`，index 失败不会静默回退。全局 `local_scan` 先合并全部候选 Page，再在读取首个正文前执行一次 `max_pages` 检查；页字符、总字符、耗时和 `max_results` 都按整个调用累计。全局 `onenote_index` 对 COM `FindPages` 传空 `start_id`，用同一完整 catalog 补全 Notebook、父级与路径；index snippet hydration 同样受页数、单页字符、总字符和耗时限制。`include_recycle_bin` 只控制已打开 Notebook 中的回收站结果，不会把已关闭 Notebook 纳入范围。空 hierarchy 正常返回空结果。空 `start_id` 与 Desktop `Ctrl+E` 的完全等价性仍需真实环境逐版本验证。

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

`content_format` 取 `plain/html/markdown`；`new_page_style` 取 `default/blank_with_title/blank_no_title`。Markdown 富转换依赖可选 OneMore，缺失时按现有转换器边界处理。受限 HTML 还支持原生扁平列表：`ol/ul` 与 `li`；`li data-tag="to-do"`、`li data-tag="to-do:completed"` 分别生成未完成/已完成的原生 OneNote To Do 标签。嵌套列表及未知 `data-tag` fail closed，不开放 raw XML。

## 5. Rename、Reorder 与 Reparent

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `rename_section_group` | `section_group_id`, `new_name`, `expected_name`, `expected_parent_id`, `expected_modified=null` | `item`, `previous_name`；验证同 ID、父级和新名称。 |
| `rename_section` | `section_id`, `new_name`, `expected_name`, `expected_parent_id`, `expected_modified=null` | 同上。 |
| `reorder_page` | `page_id`, `expected_title`, `expected_section_id`, `after_page_id=""`, `page_level=0`, `expected_modified=null` | `item`, `pages`；验证位置与缩进。空 `after_page_id` 表示置顶，`page_level=0` 表示保留。 |
| `reorder_section` | `section_id`, `expected_name`, `expected_parent_id`, `after_section_id=""`, `expected_modified=null` | `item`, `siblings`, `after_id`, `verified`；只在同一 Notebook/SectionGroup 父级的 Section 序列内移动。空 predecessor 表示置于同类型序列首位。 |
| `reorder_section_group` | 不支持 | 必须拒绝，不能尝试通过 sibling XML、Rename、Copy/Delete 或 raw XML 模拟。OneNote 后端只提供按名称固定升序的 SectionGroup 集合，没有可验证的可变 sibling order。 |
| `reparent_page` | `page_id`, `destination_section_id`, `expected_title`, `expected_section_id`, `expected_modified=null` | `item`, `previous_parent_id`, `destination_parent_id`, `id_map`, `verified`, `warnings`；允许 Page/可观测内容对象 ID 一对一重映射，验证根 Page 拓扑、富内容和无关对象。 |
| `reparent_section` | `section_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `expected_modified=null` | 同一响应骨架；验证 Section ID、Page ID/顺序、Page 内容和无关对象。 |
| `reparent_section_group` | `section_group_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `expected_modified=null` | 同一响应骨架；验证 Group/后代 ID、父子拓扑、Page 内容和无关对象。 |

Rename 与 Page Reorder 要求写开关。Section Reorder 还要求 `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION=true`；它在 mutation 前精确确认 ID/名称/父级/可选 modified，使用完整直属容器 sibling XML，并在写后验证父级、sibling ID 集合以及所有受影响 Page 的顺序和内容摘要。Page 内容摘要忽略 OneNote 所有层级节点上的时钟、作者、选择与视图元数据，但保留内容对象 ID、格式、文本和二进制内容。验证读取受现有 Copy hierarchy/Page/XML budgets 限制。

`reorder_section_group` 的早期实验实现不构成产品能力。2026-08-10 的用户触发隔离验证中，Notebook 直属 Group 的 `A,B,C → A,C,B` 请求通过 `UpdateHierarchy(xs2013)` 返回成功，但立即按 ID 回读仍为后端固定的名称升序。该结果排除了 confirmation、父级选择、fixture 和 Runner 后置判断问题；嵌套父级操作因根级失败而没有执行。基于“后端没有 SectionGroup 可变顺序原语”这一能力边界，产品契约对 Notebook 与 SectionGroup 两种父级统一拒绝 reorder，而不是继续用更多 mutation 猜测后端行为。

Reparent 统一表示同一 Notebook 内的容器换父级，不包含 Copy/Delete，也不能跨 Notebook。三个 typed 工具默认注册，但执行同时要求 Writes 与 `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT=true`。service 在任何 COM mutation 前完成精确 ID/类型、confirmation、活动对象、同 Notebook 和目标类型检查；SectionGroup 额外拒绝自身/后代目标。Page 的已验证合同只接受没有父/子缩进关系的根 Page，并允许 OneNote 原生操作把 Page 及内容对象 ID 一对一重映射；调用方必须从 `item.id`/`id_map` 继续。所有验证均受现有 hierarchy/Page/XML budget 限制。底层 bridge `update_hierarchy` 只由这些受约束 service 及 Reorder 内部编排，生产 MCP 不存在接受外部任意 hierarchy XML 的工具。跨 Notebook 转移若未来支持，应作为创建新 ID、验证目标并非永久删除源的 Move 独立设计，不能静默降级到 Reparent。

## 6. Delete

Delete 总开关为 `LOCAL_ONENOTE_ENABLE_DELETES=true`；`permanently=true` 还要求 `LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES=true`。默认进入 OneNote 回收站。

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `delete_section_group` | `section_group_id`, `expected_name`, `expected_parent_id`, `expected_modified=null`, `permanently=false` | 原 `item`, `final_state`, `deleted`, `permanently`。 |
| `delete_section` | `section_id`, `expected_name`, `expected_parent_id`, `expected_modified=null`, `permanently=false` | 同上。 |
| `delete_page` | `page_id`, `expected_title`, `expected_section_id`, `expected_modified=null`, `permanently=false` | 同上。 |
| `delete_page_content` | `page_id`, `object_id`, `expected_title`, `expected_section_id`, `expected_modified=null` | `page_id`, `object_id`, `deleted`；删除前后复核对象快照。 |

Notebook 没有 Delete 工具。

## 7. P2 Copy 与 Page Move（实验）

计划工具只读；执行工具除现有确认字段外必须提交刚生成的 `plan_digest`。摘要包含源树、每页完整 XML hash、目标直属子项、名称、执行模式和 Page Copy 的 `include_descendants`；执行前重算不一致即在 mutation 前拒绝。计划返回的 `snapshots.source` 公开稳定资源列表与 Page hash、`snapshots.destination` 公开目标直属快照，但不返回原始 Page XML。

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `plan_copy` | `source_id`, `destination_parent_id=""`, `destination_name=""`, `destination_base_folder=""`, `include_descendants=false` | `plan_digest`, `include_descendants`, `source`, `destination`, `estimated`, `copyability`, `steps`, `execute_tool`。 |
| `copy_page` | `page_id`, `destination_section_id`, `expected_title`, `expected_section_id`, `plan_digest`, `expected_modified=null`, `destination_title=""`, `include_descendants=false` | 实际选择范围的 `item`, `copy_report`, `created_ids`；目标根追加为 level 1。 |
| `copy_section` | `section_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `plan_digest`, `expected_modified=null`, `destination_name=""` | 递归创建 Section 与全部 Page。 |
| `copy_section_group` | `section_group_id`, 其余确认/目标字段同上 | 递归创建 SectionGroup、Section 和 Page。 |
| `copy_notebook` | `notebook_id`, `expected_name`, `plan_digest`, `expected_modified=null`, `destination_name=""`, `destination_base_folder=""` | 新 Notebook 及全部后代；不提供 Notebook 删除回滚。 |
| `plan_move_page` | `page_id`, `destination_section_id`, `destination_title=""` | Copy 计划加源 Page 回收步骤和外部入站链接警告。 |
| `move_page` | `page_id`, `destination_section_id`, `expected_title`, `expected_section_id`, `plan_digest`, `expected_modified=null`, `destination_title=""` | lossless/verified 后叶到根回收源 Page；否则 `partial_failure`, `outcome=copy_only`, `source_deleted=false`。 |

`copy_report` 固定包含 `id_map/copied_counts/skipped_content/issues/lossless/verified/page_results`；Notebook Copy 还包含经过创建返回值核对的 `destination_path`。已知有损但执行完整的 Copy 可返回成功和 warning；运行中失败保留已创建目标，返回 `created_ids/completed_steps/failed_step`，不做破坏性自动回滚。

Page Copy 省略 `include_descendants` 或显式为 `false` 时只选择根 Page；显式为 `true` 时才选择完整缩进子树。计划的 source snapshot、估算、步骤、预算以及执行的 `id_map/created_ids/copied_counts/page_results` 都只覆盖实际选择范围；计划与执行值不一致时在创建目标前拒绝。单页目标根归一化为 level 1，被排除的源后代及其缩进关系保持不变，指向它们的链接作为范围外链接保留原目标。Section、SectionGroup、Notebook Copy 继续递归，`include_descendants` 不改变其范围；Page Move 继续固定处理完整子树。

名称冲突按直属子项 case-insensitive 拒绝；没有覆盖、合并或自动后缀。范围内已识别 ID 引用会改写，范围外出站链接保留；Move 的语义天然是重建，它不扫描外部入站链接且始终返回 old→new ID。

递归容器按源 hierarchy snapshot 的确定性深度优先顺序创建。Page 的顺序和相对 `page_level` 必须回读验证；Section/SectionGroup 没有稳定 COM 顺序字段，因此只承诺并验证父子结构，不声明容器顺序保真。Notebook 名称冲突只在解析后的目标 `destination_base_folder/name` 路径上判断，不因其他目录存在同名已打开 Notebook 而误拒绝。

Move 删除阶段的部分失败分别返回 `attempted_source_ids`、已从活动树移除的 `deleted_source_ids`、其中带回收站标记的 `recycled_source_ids`、未暴露回收站元数据的 `recycle_unverified_source_ids` 和尚未完成步骤的 `remaining_source_ids`。只有 `deleted_source_ids` 参与源删除完成判断，未取得标记的 ID 不得计入 `recycled_source_ids`。

当前候选 XML 内容会尽力保留；能力清单除 Outline/Image/附件/墨迹/媒体对象外，还单独识别 `RichText/Table/List/Tag/MeetingInfo`。基于用户确认的隔离真实后端证据，`Outline/Image/RichText/Table/List/Tag` 已进入保真 allowlist；其余尚未确认的类型产生 `content_type_unverified`，使 `lossless=false` 并阻止 Move 删除源。后续专属取证只覆盖 `InkDrawing`、OneNote UI Shape 和 `MediaFile`（在线视频）。`FileAttachment` 因当前 GUI 无法生成独立表示而排除，`MeetingInfo` 因小众、难生成且价值低而排除；两者没有专属测试入口，但仍保持 unverified/fail-closed。详见 [`lesson/copy_content_type_exclusions.md`](../lesson/copy_content_type_exclusions.md)。已知顶层内容块内只要出现不在 OneNote 2013 静态节点 allowlist 的后代节点，整个顶层块即省略并返回 `unsupported_nested_page_node`，不会静默透传未来扩展。

Page 回读采用按页面内容组合选择的分层验收：

- `strict_canonical`：不含 List/Tag，或把 List/Tag 与 Table/Image/MeetingInfo 等其他结构混在同一 Page 时使用。它要求 canonical XML、可见文本、内容对象计数和二进制 hash 全部相等。
- `semantic_list_tag`：Page 的能力集合限于 `Outline/RichText/List/Tag` 且实际出现 List 或 Tag 时使用。它要求可见文本、二进制 hash，以及列表种类、标签类型、完成状态的语义投影相等；canonical XML 和对象计数仍记录为诊断，但不作为接受条件。

这个分层是 OneNote COM 复制语义的一部分，而不只是测试便利：`UpdatePageContent` 会重新生成或规范化 `TagDef` index、列表序号状态、对象 ID、Outline/OE 分块和部分属性，因此视觉及行为完全相同的 List/Tag 页面可能无法 canonical 相等。若对所有内容统一使用严格 XML，会把成功复制误报为失败；若对整页统一放宽，又可能掩盖 Table/Image 等稳定结构的真实丢失。把两类内容放在独立 Page，并逐页选择验收 tier，可以同时保留稳定类型的强门禁和 List/Tag 的 COM 等价性。

`List/Tag` 已是 validated/lossless 类型；“语义 tier”描述的是证明保真的方法，不代表它仍未验证。只要每页按其 tier 等价且拓扑回读通过，Copy 可报告 `lossless=true`；Move 仍要求整棵子树每页通过且源快照未变化，才允许回收源 Page。四个 Copy scenario 与 `move-page` 都自动创建严格父 Page 和 List/Tag 语义子 Page，以在每个容器层级重复验证同一合同。`FileAttachment` 与 `MeetingInfo` 不属于当前取证范围，但仍保持 unverified。验证流程见 `tests/manual_validation/README.md`。

Move 对每个源 Page 调用 `DeleteHierarchy(permanently=false)`。通用删除服务会有界回读：对象必须从活动 hierarchy 消失，或者明确回读为 `is_in_recycle_bin=true`；若仍处于活动树则失败。全部源 Page 通过这一关口后，Move 可成功，manual scenario 还会以 after snapshot 独立确认整棵源子树不再活动。COM 是否再次暴露旧 ID 及其回收站标记不是验收条件，因为实际 OneNote UI 可能已在“已删除的笔记”中显示页面，而 COM hierarchy 仍不返回对应对象。返回中的 `recycle_bin_verification=verified|not_required_com_unavailable`、`recycled_source_ids` 和 `recycle_unverified_source_ids` 只表达诊断置信度，不改变非永久删除与活动树缺失的成功语义。该限制的观察证据、错误验收模型和可复用结论见 [`lesson/onenote_com_recycle_bin_visibility.md`](../lesson/onenote_com_recycle_bin_visibility.md)。

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
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT` | `false` | 同 Notebook Page/Section/SectionGroup Reparent，不能替代写开关。 |
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION` | `false` | 同父级 Section Reorder，不能替代写开关。 |
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP` | `false` | 遗留实验开关；必须保持 `false`，不授予 SectionGroup reorder 能力，等待实现面移除。 |
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY` | `false` | 四层 Copy；不能替代写开关。 |
| `LOCAL_ONENOTE_ENABLE_MOVE_PAGE` | `false` | Page Move；还要求 Writes、Deletes 和 Experimental Copy。Move 天然采用重建语义。 |
| `LOCAL_ONENOTE_ENABLE_RAW_XML` | `false` | 启动时注册剩余 6 个开发 profile 工具；不能开放 raw hierarchy mutation。 |
| `LOCAL_ONENOTE_MAX_SEARCH_PAGES` | `200` | 本地扫描候选 Page 上限，也是 index snippet hydration 的 Page 上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_PAGE_CHARS` | `100000` | 单 Page 扫描字符上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS` | `2000000` | 单次扫描总字符上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_SECONDS` | `30` | 单次本地扫描或 index snippet hydration 秒数上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_SNIPPET_CHARS` | `400` | snippet 上限。 |
| `LOCAL_ONENOTE_MAX_COPY_RESOURCES` | `1000` | 单次 Copy 的层级对象上限。 |
| `LOCAL_ONENOTE_MAX_COPY_PAGES` | `200` | 单次 Copy 的 Page 上限。 |
| `LOCAL_ONENOTE_MAX_COPY_CONTENT_OBJECTS` | `10000` | 单次 Copy 的内容对象上限。 |
| `LOCAL_ONENOTE_MAX_COPY_PAGE_XML_BYTES` | `33554432` | 单 Page 完整 XML 字节上限。 |
| `LOCAL_ONENOTE_MAX_COPY_TOTAL_XML_BYTES` | `268435456` | 单次计划全部 Page XML 字节上限。 |
| `LOCAL_ONENOTE_MAX_COPY_PLAN_SECONDS` | `300` | 只读计划阶段秒数上限。 |
| `LOCAL_ONENOTE_MAX_COPY_EXECUTE_SECONDS` | `1800` | 执行阶段秒数上限；超限按部分失败报告。 |

默认不注册 `update_page_xml/delete_hierarchy/open_hierarchy/find_meta/merge_sections/set_filing_location`。`update_hierarchy_xml` 已从所有生产 profile 移除，设置 Raw XML 开关也不会枚举或恢复该工具；内部 bridge operation `update_hierarchy` 保留。开发 profile 即使注册剩余 raw mutation，也仍需对应 write/delete 开关；`force` 不进入默认 typed 工具。逐工具用途和安全边界见 [Advanced/低层操作](advanced_operations.md)。
