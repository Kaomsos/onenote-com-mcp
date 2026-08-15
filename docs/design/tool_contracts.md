# MCP 工具参数与返回格式（P0/P1 + P2 实验实现）

> 状态：默认工具 profile 的权威契约  
> 更新日期：2026-08-15
> ID 参数均指 OneNote COM 对象 ID；除 `resolve_identifier` 外不接受名称或路径。

> **当前与目标的边界：** 本文描述当前已实现的 56 工具合同。用户已冻结但尚未实现的 v1.0 发布目标为 52 个 User 工具、13 项重命名、5 个非注册 Internal & Incubating 入口和 7 个授权类别，详见 [TODO 034](../todo/034_pre_user_testing_tool_surface_convergence.md)。在 Registry、测试和验收完成前，不得把目标名称或权限当作当前可用合同。

生产 profile 共 56 个工具，不存在生产 advanced profile；参数和返回格式由 `tools/` 薄适配层公开，全部生产 Tool 先进入 canonical Operation Runtime/Registry，再由现有 `services/` 实现业务语义与回读验证。层级浏览已收敛为唯一的 root discovery `list_notebooks`、四个 typed Expand 和通用 `expand_hierarchy`；四个 `query_*` 独立承担平展过滤与分页。Section Reorder、三类 Reparent、P2 Copy、Page Move 与容器 Move 由默认关闭的独立策略保护；SectionGroup Reorder 不属于受支持能力，也不存在 MCP adapter 或 Registry binding。只有下文列入 validated allowlist 的 Page 内容类型具有真实 OneNote 隔离证据，实验工具在对应真实场景完成前不升级稳定性承诺。

## 1. 通用返回 envelope

成功：

```json
{
  "ok": true,
  "complete": true,
  "warnings": [],
  "item": {},
  "execution": {}
}
```

失败：

```json
{
  "ok": false,
  "complete": false,
  "code": "validation_error | policy_disabled | mutation_not_applied | backend_error | partial_failure | onenote_*",
  "error": "safe message",
  "error_type": "stable typed error name",
  "hresult": "0x80042030",
  "retryability": "after_user_action | read_after_delay | reconcile_before_retry | unknown",
  "partial": false,
  "reconciliation": "not_applied | applied | partially_applied | indeterminate",
  "execution": {}
}
```

List 与 Query 返回 `items/count`；对象读取返回 `item`；Expand 返回 `tree={item,children[]}`。稳定 mutation 成功响应可含 `convergence={converged,attempts,elapsed_seconds,stable_observations,identity_remap,transient_errors}`。纳入 bounded-attempt control 的 mutation 还返回 `reconciliation={state,execute_attempts,had_backend_error,execution_succeeded,mutation_stage,preflight_state,persistence_checkpoint,mutation_attempted,mutation_attempts,mutation_replayed,observed_outcome,execute_error_reconciled,retry_safety,recommended_action,manual_recovery_required,observation_attempts,identity_policy}`。其中 `mutation_attempts` 只计 principal attempt，不表示完整 operation 的所有 backend calls；例如 root-only Page Reparent 可先执行具名 descendant promotion。默认要求至少两个连续一致的 live postcondition；这些摘要不含 Page XML、正文、binary、secret、完整路径或原始参数。

每个成功或失败 envelope 都包含 additive `execution={operation,stage,kind,backend_category,attempts,replayed,backend_calls,completed_steps,observed_outcome,retry_safety,recommended_action,cache_generation,content_exposed}`。这是 transport-independent Operation Outcome 的 content-free 投影；`completed_steps` 只允许 operation/status/attempt/count，`content_exposed` 固定为 false。它不记录原始参数、Page 正文、raw XML、binary、secret、Bridge payload、对象 ID 或完整路径。完整控制面合同见 [`operation_runtime.md`](operation_runtime.md)。

受 attempt policy 管理的 mutation 失败至少保留 `mutation_stage/mutation_attempted/mutation_attempts/mutation_replayed/observed_outcome/preflight_state/persistence_checkpoint/retry_safety/recommended_action/manual_recovery_required/observation_attempts`。`not_applied` 的普通失败 code 为 `mutation_not_applied`；若底层存在 typed OneNote error，则保留该 error 的 `onenote_*` code、最内层 HRESULT 与 retryability，并 additive 地附上上述字段。`partially_applied/indeterminate` 使用 `partial_failure`，禁止盲目重放。preflight validation 仍保持 `validation_error`，但 Reparent 会明确返回 `mutation_stage="preflight"`、`mutation_attempted=false` 与 `mutation_replayed=false`。

Typed backend error 保留规范十六进制 `hresult`（并可含 `hresult_signed`）、operation 和 content-free backend category。分类依据 [Microsoft OneNote error codes](https://learn.microsoft.com/en-us/office/client-developer/onenote/error-codes-onenote)：`0x80042030` 固定为 `onenote_modal_ui_blocked/after_user_action`；`0x8004201D` 为 `onenote_not_yet_synchronized/read_after_delay`；`0x80042023` 为 `onenote_operation_timeout/reconcile_before_retry`；文档列出的 object/file does-not-exist HRESULT 归为 `onenote_object_unavailable` 或 `onenote_file_unavailable`，仅允许 read-after-delay。未知 HRESULT 保持 `onenote_backend_error/unknown`。只有 not-yet-synchronized/timeout 加精确 unchanged pre-state 才可能重放声明为幂等的 mutation；object/file unavailable 或 modal 不能作为 mutation replay 依据。`partial_failure` 明确 `partial/reconciliation/manual_recovery_required`；convergence timeout 也明确 `partial=true/reconciliation=indeterminate`，partial 或 indeterminate 禁止盲目重做 Copy/Move/Create 或删除 source。

单 MCP 进程中，Runtime 先按 Registry authorization policy 检查独立权限；拒绝发生在 backend、lease 和 generation invalidation 之前。随后 Registry 将 Read 映射到 shared lease，将 OneNote mutation/lifecycle 映射到 exclusive lease；mutation 从 confirmation 到 convergence/reconciliation 持有独占权，因此同进程 read 不会观察 mutation 中间窗口。每个 exclusive operation 只推进一次 coordinator generation。该合同不覆盖另一个 MCP 进程或用户直接编辑。未来只读 cache 必须在 COM 前通过同一 generation 失效，confirmation/read-back 永远读取 live 状态。

## 2. 发现、List/Expand/Get、Query

| 工具 | 参数 | 成功时的主要返回 |
| --- | --- | --- |
| `health_check` | 无 | 先以不创建 COM 的 Windows native probe 要求既有 OneNote GUI；成功后返回 `onenote_desktop={process_running,visible_window_present,ready,probe}`、运行时位置、统计、`operation_runtime={enabled,registered_operations,default_operations,advanced_operations,content_free_audit}`、`copy_move={tools,single_call,public_planning_tools,agent_managed_plan_state,preview}`、`mutation_policy`、固定 Search 能力、`metadata_query`、`hierarchy_browsing`、`search_budget`、`copy_budget`。 |
| `resolve_identifier` | `identifier`, `item_type=""` | `item`、`identifier_resolution_order`；仅只读辅助。 |
| `list_notebooks` | 无 | 当前打开 Notebook 的 `items`, `count`；顺序与稳定 hierarchy 下无过滤 `query_notebook` 全集一致。 |
| `get_notebook` | `notebook_id` | `item: Notebook`。 |
| `get_section_group` | `section_group_id` | `item: SectionGroup`。 |
| `get_section` | `section_id` | `item: Section`。 |
| `get_page` | `page_id` | `item: Page`；不读取正文。 |
| `expand_notebook` | `id` | 穿过任意嵌套 SectionGroup 到全部 Section；Section 为叶节点。 |
| `expand_section_group` | `id` | 不越出精确 Group，穿过嵌套 Group 到全部 Section；Section 为叶节点。 |
| `expand_section` | `id` | 精确 Section 下按 `parent_page_id/page_level` 组织的完整 Page 树。 |
| `expand_page` | `id` | 精确 Page 的完整后代子页树，不含兄弟 Page。 |
| `expand_hierarchy` | `root_id`, `max_depth=8`, `include_recycle_bin=false` | 任意四层精确 root 的数值深度树。 |
| `query_notebook` | `name_equals=""`, `name_contains=""`, `modified_after=""`, `modified_before=""`, `offset=0`, `page_size=200` | 固定 open-only root；统一 typed Query envelope。 |
| `query_section_group` | `scope`, `name_equals=""`, `name_contains=""`, `parent_id=""`, `modified_after=""`, `modified_before=""`, `include_recycle_bin=false`, `offset=0`, `page_size=200` | 只返回 SectionGroup；start node 允许 Notebook/SectionGroup。 |
| `query_section` | 同上 | 只返回 Section；start node 允许 Notebook/SectionGroup。 |
| `query_page` | `scope`, `title_equals=""`, `title_contains=""`, `section_id=""`, `parent_page_id=""`, `modified_after=""`, `modified_before=""`, `include_recycle_bin=false`, `offset=0`, `page_size=200` | 只返回 Page hierarchy metadata；start node 允许 Notebook/SectionGroup/Section，不读取正文。 |
| `get_path` | `object_id` | `item`, `path`, `ancestors`。 |
| `get_parent` | `object_id` | `item`, `parent`, `parent_id`。 |
| `get_special_locations` | 无 | `locations={backup,unfiled,default_notebook_folder}`。 |

四个 Query 固定返回 `items/count/total_matches/offset/page_size/has_more/next_offset/pagination_consistency="live_hierarchy"/resource_type/query_kind="hierarchy_metadata"/scope`。时间必须是带 offset 或 `Z` 的 RFC 3339 且 `modified_after < modified_before`；比较为严格大于/小于。`offset >= 0`，`page_size` 为 `1..200`，非法值明确拒绝。root 路线只执行一次空 start ID 的最浅 `GetHierarchy`：Notebook=`hsNotebooks`，SectionGroup/Section=`hsSections`，Page=`hsPages`。start-node 路线先执行一次 root/`hsSections` catalog 验证精确类型、open-only 与回收站边界，再对同一精确 ID 执行一次目标 scope；失败不回退到 root、名称、磁盘、`FindMeta` 或逐 Notebook 调用。start node 本身不进入候选。

`query_section_group/query_section/query_page.scope` 是 `mode="root"` 或 `mode="start_node" + start_node_id` 的必填严格判别联合，两个分支禁止额外字段。容器 `parent_id` 只表示 scope 内直属 Notebook/SectionGroup；Page 分别使用直属 `section_id` 与同 Section 有序扁平序列派生的直接缩进 `parent_page_id`。`include_recycle_bin=true` 不得引入关闭 Notebook、越出 start node 的对象或允许回收站节点作为起点。分页应用于完整过滤结果之后，每页重新读取 live hierarchy，不冻结跨页 snapshot，也不减少 COM 输出或 Python metadata 扫描。无过滤 Query 可按 `next_offset` 取尽当前固定类型，并覆盖保留 List 的枚举用途。

四个 typed Expand 的唯一参数都命名为 `id`，必须是非空精确 COM ID。工具名固定实际 root 类型；未知 ID、类型不符、已关闭 Notebook 和回收站 root 均 fail closed。它们不接受深度、过滤、分页、selector、`include_xml` 或回收站选项。Notebook/Group 只读 `hsSections`，Section/Page 只读 `hsPages`，不读取 Page 正文。

全部 Expand 共享递归节点 `{item,children[]}`，叶节点固定 `children=[]`。容器按 `parent_id`，顶层 Page 按 `section_id`，缩进 Page 优先按 `parent_page_id`；children 保持同一 snapshot 的稳定顺序。共享关系图会拒绝空/重复 ID、断裂或循环关系、跨 Section Page parent、无效缩进和对象重复。精确 root Expand 先要求该 root ID 在同一次 COM snapshot 中恰好命中一次，再只对 root 所属 Notebook 校验完整关系图；其他打开 Notebook 中独立存在的缺失/重复 ID、Page 缩进或关系异常不得阻断目标树，目标 Notebook 内的同类异常仍 fail closed。完整树超过 10,000 个轻量 metadata 对象时明确失败，不返回伪完整树。`expand_hierarchy` 保留通用 `max_depth` 与显式 `include_recycle_bin`，但仍拒绝关闭 Notebook；它和 typed Expand 使用同一 builder。

选择规则：打开了哪些 Notebook 使用 `list_notebooks`；按对象语义浏览使用 typed `expand_*`；任意 root 加数值深度使用 `expand_hierarchy`；按字段过滤、关系筛选或分页使用 `query_*`；精确单对象 metadata 使用 `get_*`；Page 正文搜索使用 `search_pages`。Expand 只返回 hierarchy tree，不修改 OneNote GUI 展开状态。

`health_check` 的 Desktop readiness 检查发生在首次 hierarchy bridge/COM 调用之前。只有 `ONENOTE.EXE` 进程存在且拥有可见、无 owner 的顶层窗口时才进入 COM 统计读取；进程缺失或只有后台进程时返回 `code="onenote_desktop_not_running"`、`retryability="after_user_action"`、`operation="health_preflight"` 和 content-free readiness/`required_action`，且不冷启动 OneNote。native probe 无法可靠完成时返回 `onenote_desktop_probe_failed` 并同样 fail closed。当前没有自动启动工具；目标 `launch_onenote_gui` 由 [TODO 031](../todo/031_start_onenote_desktop_tool.md) 跟踪，且不会改变 `health_check` 的 check-only 语义。

## 3. Page 内容与 Search

| 工具 | 参数 | 成功时的主要返回 |
| --- | --- | --- |
| `get_page_text` | `page_id`, `max_chars=60000` | `text`, `chars`；过长内容带截断标记。 |
| `get_page_xml` | `page_id`, `page_info="basic"` | `xml`。`page_info` 见下方枚举。 |
| `get_page_objects` | `page_id` | `objects: PageContentObject[]`, `count`。 |
| `get_binary_content` | `page_id`, `callback_id` | 已复核的 `object`、`base64`。 |
| `search_pages` | `query`, `scope`, `offset=0`, `page_size=200`, `include_snippets=true`, `include_recycle_bin=false` | `pages`, `count`, `total_matches`, `offset`, `page_size`, `has_more`, `next_offset`, `pagination_consistency`, `scope`, `search_backend`, `scan_budget`。 |

公开 `PageContentObject` 固定投影为 `id/page_id/kind/parent_object_id/container_object_id/callback_id/media_type/can_delete/delete_target_id`。调用方只能在 `can_delete=true` 时把非空 `delete_target_id` 传给 `delete_page_content.object_id`；`object_id/delete_supported/delete_object_id` 是 Page XML parser 的内部字段，不属于 MCP Tool 响应合同。

`scope` 是 `mode="root"` 或 `mode="start_node" + start_node_id` 的严格判别联合，两个对象分支都禁止额外字段。root 对 COM `FindPages` 传空 `start_id`；start node 只接受一个精确、属于已打开 Notebook 的 Notebook/SectionGroup/Section ID，不接受 Page、名称、路径或离散 ID 数组。每次公开调用只执行一次 `FindPages`，固定 `include_unindexed=false`、`display=false`，index 失败不回退。结果按原始 XML 顺序处理，并用同一次完整 catalog 补全和证明归属；范围外、已关闭、无法证明归属和不符合回收站参数的 Page 被排除。

分页是无状态 `live_index`：`offset >= 0`，`page_size` 默认和最大均为 200，每一页重新执行 `FindPages`，不承诺跨页冻结快照。过滤后的完整候选集必须先通过 `LOCAL_ONENOTE_MAX_SEARCH_PAGES`，随后才执行切片；因此较大 offset 不能绕过候选预算。snippet 只 hydration 当前页。空 hierarchy 和越界 offset 均返回成功空页；空 `start_id` 与 Desktop `Ctrl+E` 的完全等价性仍需真实环境逐版本验证。

`page_info`：`basic/binary/selection/binary_selection/file_type/binary_file_type/selection_file_type/all`。

## 4. Create 与 Page typed mutation

下列工具均要求 `LOCAL_ONENOTE_ENABLE_WRITES=true`：

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `create_notebook` | `name_or_path`, `base_folder=""` | `notebook_id`, `allocated_id`, `identity_remapped`, `item`, `path`；按新对象 ID/路径回读。 |
| `create_section_group` | `parent_id`, `group_name` | `section_group_id`, `allocated_id`, `identity_remapped`, `section_group`, `parent`, `path`。 |
| `create_section` | `parent_id`, `section_name` | `section_id`, `allocated_id`, `identity_remapped`, `section`, `parent`, `path`。 |
| `create_page` | `section_id`, `title`, `content=""`, `content_format="plain"`, `new_page_style="blank_with_title"` | `page_id`, `allocated_id`, `identity_remapped`, `page`, `section`, `path`。 |
| `update_page_title` | `page_id`, `title`, `expected_title`, `expected_section_id`, `expected_modified=null` | 更新后的 `item`；验证同 ID 和新标题。 |
| `append_to_page` | `page_id`, `content`, `expected_title`, `expected_section_id`, `expected_modified=null`, `content_format="plain"`, `x=null`, `y=null` | `item`, `appended=true`, `before_modified`；验证内容摘要变化。 |
| `add_image_to_page` | `page_id`, `image_path`, `expected_title`, `expected_section_id`, `expected_modified=null`, `image_format=""`, `x=36`, `y=120`, `width=null`, `height=null` | `item`, `image_path`, 实际 `width/height`；验证内容摘要变化。 |
| `replace_page_body` | `page_id`, `content`, `expected_title`, `expected_section_id`, `expected_modified=null`, `title=null`, `content_format="plain"` | `item`, `deleted_objects`, `replaced`, `partial`；非原子。 |

`content_format` 取 `plain/html/markdown`；`new_page_style` 取 `default/blank_with_title/blank_no_title`。Markdown 富转换依赖可选 OneMore，缺失时按现有转换器边界处理。受限 HTML 还支持原生扁平列表：`ol/ul` 与 `li`；`li data-tag="to-do"`、`li data-tag="to-do:completed"` 分别生成未完成/已完成的原生 OneNote To Do 标签。它也接受使用规范命名空间的有界 Presentation MathML；`math` 不带 `display` 表示行内公式，`display="block"` 表示独立单行公式并单独生成一个非空 `TextBlock/OE/T`，避免从混合富文本拆分 block 公式。未知元素、属性、命名空间或 display 值 fail closed。嵌套列表及未知 `data-tag` 同样 fail closed，不开放 raw XML。

独立 `TextBlock/OE/T` 不消除 OneNote COM 自身的 DisplayEquation 写回限制。在当前实测环境中，任何通过 `UpdatePageContent` 初次生成的 standalone block MathML 都可能在公式前获得一个纯空白 `span + br`；若将该包装原样再次提交，break 会继续累积。这个边界适用于最终走相同 COM 写入的 `create_page`、`append_to_page`、`replace_page_body` 和 reconstruction Copy/Move，不是 Copy 专属问题。上述普通 Page 写工具不承诺 XML/CDATA 字节等同或公式前绝无 OneNote 生成的间距；Copy 另在发送前实施受限清理以阻止累积。行内公式不在该特例内。证据与版本边界见 [`lesson/display_equation_com_leading_whitespace_normalization.md`](../lesson/display_equation_com_leading_whitespace_normalization.md)。

Create 的 COM 返回 ID 是第一身份来源。回读对象必须同时满足预期 type、friendly path、active state 与计划父级；Page 必须属于请求的 Section。只有 COM 返回 ID 在 hierarchy 中不可见、同一路径恰有一个新出现的 typed 对象时，才以 `identity_remapped=true` 接受一对一 remap。重复 path、既有对象 ID、错误 type/parent 或 recycle-bin 对象一律拒绝；失败响应保留 `allocated_ids`，不得按标题或 path 任选旧对象。合法的同 Section 重名 Page 因此返回互异的精确 Page IDs。

Create、Page title/content mutation、Rename、Reorder、Reparent、Copy topology/fidelity、Delete 和 Close 的成功必须经过公共连续稳定门。Bounded-attempt control 当前覆盖 Page title、Append/Image、Rename、Page/Section Reorder、三类 Reparent、Page 内容对象 Delete、三类 typed hierarchy Delete 与 Close；每项必须先命中显式 attempt policy。当前全部生产 policy 固定 `replay=never`，COM error 后 observer 仍可能证明完整 postcondition 并形成 reconciled success，但 exact pre-state 只会返回可行动的 `not_applied`，不会在同一 Tool 调用内重放。Append/Image 的通用内容摘要只能在 COM success 后证明“页面发生并稳定了变化”，execute error 后的摘要变化不足以归因于本次请求，因此判为 indeterminate。`delete_page_content` 还要求非目标对象 ID 集合不漂移。Create、`replace_page_body`、Copy/Move 属于多阶段或 allocated-identity workflow，继续使用各自编排；其 operation-wide saga 和统一 Runtime 归 TODO 036。完整矩阵见 [`mutation_readiness_and_call_design.md`](mutation_readiness_and_call_design.md)。

## 5. Rename、Reorder 与 Reparent

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `rename_section_group` | `section_group_id`, `new_name`, `expected_name`, `expected_parent_id`, `expected_modified=null` | `item`, `previous_name`；验证同 ID、父级和新名称。 |
| `rename_section` | `section_id`, `new_name`, `expected_name`, `expected_parent_id`, `expected_modified=null` | 同上。 |
| `reorder_page` | `page_id`, `expected_title`, `expected_section_id`, `after_page_id=""`, `page_level=0`, `expected_modified=null` | `item`, `pages`；验证位置与缩进。空 `after_page_id` 表示置顶，`page_level=0` 表示保留。 |
| `reorder_section` | `section_id`, `expected_name`, `expected_parent_id`, `after_section_id=""`, `expected_modified=null` | `item`, `siblings`, `after_id`, `verified`；只在同一 Notebook/SectionGroup 父级的 Section 序列内移动。空 predecessor 表示置于同类型序列首位。 |
| `reorder_section_group` | 不支持且不公开 | 不存在生产 MCP Tool；不能尝试通过 sibling XML、Rename、Copy/Delete 或 raw XML 模拟。OneNote 后端只提供按名称固定升序的 SectionGroup 集合，没有可验证的可变 sibling order。 |
| `reparent_page` | `page_id`, `destination_section_id`, `expected_title`, `expected_section_id`, `expected_modified=null`, `include_descendants=false` | `item`, `destination_position`, `previous_parent_id`, `destination_parent_id`, `id_map`, `verified`, `include_descendants`, `preserved_descendants`, `warnings`。默认只换父级选中 Page，被排除后代留在源 Section 并整体提升一级；显式 `true` 换父级完整缩进子树。选中 Page 在目标中归一化为根 Page；允许纳入范围的 Page/可观测内容对象 ID 一对一重映射。 |
| `reparent_section` | `section_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `expected_modified=null` | 同一响应骨架并含 `destination_position`；验证 Section ID、Page ID/顺序、Page 内容和无关对象。 |
| `reparent_section_group` | `section_group_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `expected_modified=null` | 同一响应骨架并含 `destination_position`；验证 Group/后代 ID、父子拓扑、Page 内容和无关对象。 |

Rename 与 Page Reorder 要求写开关。Section Reorder 还要求 `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION=true`；它在 mutation 前精确确认 ID/名称/父级/可选 modified，使用完整直属容器 sibling XML，并在写后验证父级、sibling ID 集合以及所有受影响 Page 的顺序和内容摘要。Page 内容摘要忽略 OneNote 所有层级节点上的时钟、作者、选择与视图元数据，但保留内容对象 ID、格式、文本和二进制内容。验证读取受现有 Copy hierarchy/Page/XML budgets 限制。

`reorder_section_group` 的早期实验实现不构成产品能力。2026-08-10 的用户触发隔离验证中，Notebook 直属 Group 的 `A,B,C → A,C,B` 请求通过 `UpdateHierarchy(xs2013)` 返回成功，但立即按 ID 回读仍为后端固定的名称升序。该结果排除了 confirmation、父级选择、fixture 和 Runner 后置判断问题；嵌套父级操作因根级失败而没有执行。基于“后端没有 SectionGroup 可变顺序原语”这一能力边界，产品契约对 Notebook 与 SectionGroup 两种父级统一拒绝 reorder，而不是继续用更多 mutation 猜测后端行为。

Reparent 统一表示同一 Notebook 内的容器换父级，不包含 Copy/Delete，也不能跨 Notebook。三个 typed 工具默认注册，但执行同时要求 Writes 与 `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT=true`。service 在任何 COM mutation 前完成精确 ID/类型、confirmation、活动对象、同 Notebook 和目标类型检查；调用方提供的可选 `expected_modified` 在首次 live confirmation 时仍严格绑定。后续完整证据 capture 与首次确认之间使用 typed identity、名称/标题、parent、Notebook/Section、Page level/parent Page/order 等语义投影复核，不因 OneNote 单独推进目标或 destination 的 `modified`/派生聚合字段而误拒绝；任一受保护关系变化仍在 mutation 前 fail closed。SectionGroup 额外拒绝自身/后代目标。Page 可从任意合法缩进 level 选择根对象：默认 root-only 路线先验证排除后代整体提升，再换父级所选 Page；显式 subtree 路线一次提交完整缩进范围并验证完整单射 `id_map`、相对拓扑和内容。任何路线的目标根均归一化为 level 1。调用方必须从 `item.id`/`id_map` 继续。所有验证均受现有 hierarchy/Page/XML budget 限制。底层 bridge `update_hierarchy` 只由这些受约束 service 及 Reorder 内部编排，生产 MCP 不存在接受外部任意 hierarchy XML 的工具。

Reparent service 不把 `SyncHierarchy` 请求接受当成 fixture 已持久化的前置条件，也不在生产 mutation 内负责 Notebook close/reopen 生命周期。OneNote COM 没有无副作用的 mutation-ready predicate；调用方进入工具前可证明的是 `logical_ready`，不是下一次 `UpdateHierarchy` 必然成功。工具冻结完整基线后只调用一次主 Reparent mutation；成功和异常共用同一个 operation-specific observer，以两次稳定 hierarchy、完整内容 snapshot 与 bookend invariant 分类 `applied/not_applied/partially_applied/indeterminate`。execute 抛错但完整 postcondition 成立时按成功返回，并标记 `execute_error_reconciled=true`；partial/indeterminate 明确要求只读查询或人工恢复。状态模型、控制面对象和 operation policy 见 [`mutation_readiness_and_call_design.md`](mutation_readiness_and_call_design.md)。人工验证 Runner 对每次正向和恢复调用核验 response contract，并从 production bridge audit 证明恰好一次 `update_hierarchy` 且没有 Sync/Close/Open；该 Runner 行为不改变公开工具参数或响应。

十个 Reparent/Copy/Move 执行工具统一返回 `destination_position`。Page 的 `index/sibling_count` 来自最终 Section 按 `order` 的完整扁平 Page 序列，且只描述 fresh 目标根，不返回 `page_level`、`parent_page_id` 或后代位置；Section/SectionGroup 在最终父级的同类型直属 children 中计算；Notebook Copy 返回固定 `not_applicable`。Move 必须在源删除后重新投影。该字段只描述最终观察状态，不是位置请求、默认落点保证或隐式 Reorder。

## 6. Delete

Delete 总开关为 `LOCAL_ONENOTE_ENABLE_DELETES=true`；`permanently=true` 还要求 `LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES=true`。默认进入 OneNote 回收站。

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `delete_section_group` | `section_group_id`, `expected_name`, `expected_parent_id`, `expected_modified=null`, `permanently=false` | 原 `item`, `final_state`, `deleted`, `permanently`。 |
| `delete_section` | `section_id`, `expected_name`, `expected_parent_id`, `expected_modified=null`, `permanently=false` | 同上。 |
| `delete_page` | `page_id`, `expected_title`, `expected_section_id`, `expected_modified=null`, `permanently=false` | 同上。 |
| `delete_page_content` | `page_id`, `object_id`, `expected_title`, `expected_section_id`, `expected_modified=null` | `page_id`, `object_id`, `deleted`；删除前后复核对象快照。 |

Notebook 没有 Delete 工具。

## 7. P2 Copy 与重建式 Move（实验）

Copy/Move 是单次公开 mutation 调用。Agent 只提交精确 source/destination ID、范围和现有 confirmation；不提交 `plan_digest`、operation token、服务端 handle 或任何跨调用状态。每次调用在同一个 exclusive Runtime operation 内重新读取 live source/destination 并建立调用专属内部计划；计划绑定 typed identity/parent/order、每页稳定内容 hash、目标直属子项、名称、执行模式、预算和 Page Copy/Move 的 `include_descendants`。稳定内容 hash 保留内容对象身份、正文、格式和二进制，但忽略 OneNote 自有时钟、选择、视图和本地 cache/path 元数据。任何 stale confirmation、protected semantic 漂移、错误 scope/parent 或预算超限都在首次目标 mutation 前 fail closed。

四个旧公开 Plan Tool 已从所有生产注册路径移除，不保留 alias 或可枚举旁路。本版本也不交付 Preview；不存在 `preview_copy/preview_move`，正常 Copy/Move 不依赖预览。未来 Preview 只能作为独立、只读、默认隐藏且不产生执行 token 的能力，不能成为 mutation readiness 或授权证明。

| 工具 | 参数 | 成功时的主要返回/验证 |
| --- | --- | --- |
| `copy_page` | `page_id`, `destination_section_id`, `expected_title`, `expected_section_id`, `expected_modified=null`, `destination_title=""`, `include_descendants=false` | 实际选择范围的 `item`, `copy_report`, `created_ids`；目标根追加为 level 1。 |
| `copy_section` | `section_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `expected_modified=null`, `destination_name=""` | 递归创建 Section 与全部 Page。 |
| `copy_section_group` | `section_group_id`, 其余确认/目标字段同上 | 递归创建 SectionGroup、Section 和 Page。 |
| `copy_notebook` | `notebook_id`, `expected_name`, `expected_modified=null`, `destination_name=""`, `destination_base_folder=""` | 新 Notebook 及全部后代；不提供 Notebook 删除回滚。 |
| `move_page` | `page_id`, `destination_section_id`, `expected_title`, `expected_section_id`, `expected_modified=null`, `destination_title=""`, `include_descendants=false` | `copy_contract_satisfied=true` 后仅叶到根回收选定 Page；root-only 时先提升并验证被排除后代；否则返回结构化 partial failure。 |
| `move_section` | `section_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `expected_modified=null`, `destination_name=""` | 完整通过共享 Copy 合同后重校验源，只调用一次非永久 Section 根删除，并验证全部源子树 ID 不再活动。 |
| `move_section_group` | `section_group_id`, `destination_parent_id`, `expected_name`, `expected_parent_id`, `expected_modified=null`, `destination_name=""` | 完整复制 Group/Section/Page 子树后只删除一次 Group 根，并复核源全部不活动和目标未变化。 |

Page/Section/SectionGroup Move 在删除源之前重新捕获源，并使用 protected semantic digest 验证资源类型/ID/名称或标题/parent/Notebook/Section/Page level/parent Page/sibling order 和稳定 Page 内容 hash。仅 `modified` 漂移时继续执行，并把刚捕获的最新时间绑定到后续精确删除确认；受保护语义变化仍阻止任何源删除。源删除并证明源对象不再活动后，目标复核使用同一 protected semantic digest。源或目标的仅时间戳变化会保留成功结果并写 warning；任一受保护拓扑或内容变化仍返回对应 partial failure，Copy、Delete 或原 mutation 都不会自动重放。

`copy_report` 固定包含 `planning/id_map/allocated_ids/resolved_target_ids/copied_counts/skipped_content/issues/lossless/verified/fidelity/copy_contract_satisfied/page_results`。`planning={internal,operation,include_descendants,estimated,content_capabilities,lossless_candidate}` 是当前调用内部计划的 content-free 摘要，不含 digest、token、对象 ID 或正文，也不能被后续执行消费。`fidelity` 为 `lossless` 或 `unverified`；`copy_contract_satisfied` 只在拓扑/机器 read-back 通过且没有 omitted/unverified issue 时为 true。`content_type_unverified` 与 omitted content 会阻止。Notebook Copy 还包含经过创建返回值核对的 `destination_path`。运行中失败保留已创建目标，返回 `created_ids/allocated_ids/resolved_target_ids/possibly_untracked_allocated_ids/id_map/source_touched/source_untouched/topology_touched/manual_recovery_required/completed_steps/failed_step`。

Page Copy 与 Page Move 省略 `include_descendants` 或显式为 `false` 时只选择根 Page；显式为 `true` 时才选择完整缩进子树。内部 source snapshot、估算、步骤、预算以及执行的 `id_map/created_ids/copied_counts/page_results` 都只覆盖实际选择范围。`destination_section_id` 始终标识 Section，不标识父 Page：执行先为范围内每个 Page 分配并精确回读全新 ID，保留目标 Section 的既有 Page 顺序，再把新目标块整体追加到末尾；目标根归一化为 level 1，选定后代按相对源根的 `page_level` 恢复缩进。创建回读优先使用 COM 返回的精确 ID；同名路径只允许无歧义回退，任何源 ID 命中、目标 ID 复用或错误 Section 落点都必须在写入 Page 正文或层级前 fail closed。Page Copy 不改变被排除后代；root-only Move 则在删除根页前将完整排除子树整体提升一级，保持其 ID、Section、相对层级和内容，并在删除后再次回读。提升前后的内容门使用稳定正文摘要，忽略此次操作必然改变的 Page 根 `pageLevel` 与 OneNote 管理的时钟/视图元数据；Page ID、Section、顺序、层级和 `parent_page_id` 仍由独立拓扑关口严格比较，普通正文、格式、内容对象和二进制变化仍会阻止删除。提升或保留证据不完整时不得删除源；删除已发生但保留页无法验证时返回需要人工接管的 partial failure。Section、SectionGroup、Notebook Copy 继续递归，`include_descendants` 不改变其范围。

`copy-page` 人工验证对同 Section、跨 Section、跨 Notebook 三种 destination 都制造同标题碰撞：同 Section 使用源 Child，另外两个 destination 各有一个同标题、不同正文的 manifest-bound anchor。六个 case 都要求新 target IDs 与 source/anchor IDs 不相交，并证明 anchors 的正文 hash、order、level 和 parent 不变。

该人工验证的不变性集合由 manifest 精确绑定 source Parent/Child 与两个 anchors，并同时比较拓扑、稳定内容 hash 和内容对象身份。稳定内容规范化只把空、无子节点且仅携带 `selected/isSelected` 的 T 视为 OneNote 视图占位；普通空 T、非空文本、格式、对象 ID 和二进制仍参与比较。Copy 生成 outbound Page XML 时也必须在剥 volatile selection 属性和替换标题之前移除这种占位，防止它变成普通空 T 或遮蔽真实标题 T；该转换同样不得删除普通空 T 或带可见内容的 selected T。最终 restore 要求全 bundle 的对象身份/拓扑、全部 Page 对象身份和能力投影相等，并对四个 manifest 保护页额外要求稳定内容相等。Description 等非验收页的后台 COM 重新序列化不替代受保护对象证据，也不应单独导致 Copy 或 restore 失败。

名称冲突按直属子项 case-insensitive 拒绝；没有覆盖、合并或自动后缀。范围内已识别 ID 引用会改写，范围外出站链接保留；Move 的语义天然是重建，它不扫描外部入站链接且始终返回 old→new ID。

递归容器按源 hierarchy snapshot 的确定性深度优先顺序创建。Page 的顺序和相对 `page_level` 必须回读验证；Section/SectionGroup 没有稳定 COM 顺序字段，因此只承诺并验证父子结构，不声明容器顺序保真。Notebook 名称冲突只在解析后的目标 `destination_base_folder/name` 路径上判断，不因其他目录存在同名已打开 Notebook 而误拒绝。

Move 删除阶段的部分失败分别返回 `attempted_source_ids`、已从活动树移除的 `deleted_source_ids`、其中带回收站标记的 `recycled_source_ids`、未暴露回收站元数据的 `recycle_unverified_source_ids` 和尚未完成步骤的 `remaining_source_ids`。只有 `deleted_source_ids` 参与源删除完成判断，未取得标记的 ID 不得计入 `recycled_source_ids`。

容器 Move 与 Page Move 的删除策略不同。Section/SectionGroup Move 强制源、目标属于不同已打开 Notebook；同 Notebook 在任何 mutation 前拒绝并指向 `reparent_section` / `reparent_section_group`。容器 Move 的内部 planning 与 execute 都隐含完整子树，不提供 `include_descendants`；共享 `copy_contract_satisfied`、完整单射 `id_map` 和源 digest 重校验通过后，只允许对源容器根调用一次 typed Delete，且 `permanently=false` 在 service 内固定。Move 不维护额外内容类型或 lossless 门，只消费 Copy 的共享合同结论。之后枚举活动 hierarchy，要求内部计划中的根及全部后代 ID 均消失，再比较删除前后的目标子树 digest。

2026-08-11 的用户触发隔离运行确认了当前环境中的三条 Move 成功路径：`move-page` 同时通过跨 Notebook root-only 与显式 subtree case，root-only 被排除子页在提升后保持活动且内容稳定；`move-section` 与 `move-section-group` 均通过 verified/lossless Copy、完整单射映射、一次非永久根删除、完整源子树活动态缺席和目标复核。Section 删除取得回收站正向标记；SectionGroup 和 Page 的部分运行中 COM 未暴露回收站元数据，但精确源 ID 均从活动 hierarchy 消失，符合既定成功门。该证据不扩大内容类型 allowlist，也不取消默认关闭的实验 policy。

当前候选 XML 内容会尽力保留。`Outline/Image/RichText/Table/List/Tag/DisplayEquation/InkDrawing/UIShape/MediaFile/InsertedFile` 属于 lossless 集合。`DisplayEquation` 是 Copy 对包含 `display="block"` 的有界 Presentation MathML 建立的语义内容类型，不是公开 PageContentObject `kind`；行内公式仍属于 RichText。UI Shape 的公开 kind 仍为 `InkDrawing`，由 `ShapeInfo`（箭头另含 `AnchorPoint`）分类。未知类型产生 `content_type_unverified` 并阻止共享 Copy 合同。`OCRData/OCRText/OCRToken` 只在 Image 子树内接受；其他上下文及未知后代仍 fail closed。

当前环境观察到的 `InsertedFile` 是 `OE` 的无子节点后代，使用 `pathSource/pathCache/preferredName`，不含内联 `Data`。重建 Copy 在内部 planning 阶段要求其中至少一个本地文件路径可读：优先使用有效 `pathSource`，否则回退到有效 `pathCache` 或旧式 `path`，并只把选中的值作为 outbound `pathSource` 交给 `UpdatePageContent`。若全部不可读，当前调用以不包含实际路径的 `validation_error` 在创建目标前 fail closed。机器本地路径继续作为 volatile 属性从 canonical/stable 比较和普通 evidence 中排除。2026-08-12 的隔离真实 Copy 已通过 strict canonical 机器比较和用户打开附件后的 run-bound ACCEPT，因此 `InsertedFile` 已进入 validated/lossless 集合；这不与 `FileAttachment` 建立别名。

Page 回读采用按页面内容组合选择的分层验收：

- `strict_canonical`：不含 List/Tag，或把 List/Tag 与 Table/Image/MeetingInfo 等其他结构混在同一 Page 时使用。它要求 canonical XML、可见文本、内容对象计数和二进制 hash 全部相等。
- `semantic_mathml`：Page XML 中只出现行内的有界 Presentation MathML 时使用。它要求公式数量、元素树和 token hash 精确相等，并要求用占位符替换完整 MathML root 及其可选的完整 OneNote `<!--[if mathML]>...<![endif]-->` 条件包装后，其余 Page XML canonical 相等；可见文本、内容对象计数和二进制 hash 仍是接受条件。只有完整配对且包裹同一可解析 MathML root 的条件注释属于序列化差异；不完整或无关注释仍 fail closed。
- `semantic_display_equation`：只要 Page 中包含 `display="block"` 的有界 Presentation MathML 即使用。除继续严格比较全部 MathML 语义、可见文本、对象和二进制外，只容忍每个单行公式前零个或一个纯空白 `<span><br/></span>`、上述完整配对 MathML 条件包装的存在/空白序列化差异，以及“唯一 authored 内容精确为一个 block MathML root”的独立 Outline 由 COM 重算的 `Size.width/height`。该 Outline 必须只含 `Position/Size/OEChildren/OE/T` 支撑节点、恰好一个完整 block 公式、没有其他正文或 markup，且 Size 属性名精确为 width/height；Position、混合内容 Outline 的 Size、额外节点和所有其他属性继续严格比较。该空 span 只允许已观察到的 `font-family` style 和可选 language 展示属性，具体字体值不参与判定。两个或更多 break、其他 span 属性、可见 span 正文、不完整/无关注释、其他残留 markup、公式数量/display/元素/token 变化均 fail closed。比较通过时可产生 `verified=true/lossless=true/copy_contract_satisfied=true`；这里的 lossless 表示本项目定义的语义保真，不表示 CDATA 字节相同。失败结果额外记录首个 canonical 差异的节点路径、字段、字符数/属性名与 hash，不记录正文或二进制。
- `semantic_list_tag`：Page 的能力集合限于 `Outline/RichText/List/Tag` 且实际出现 List 或 Tag 时使用。它要求可见文本、二进制 hash，以及列表种类、标签类型、完成状态的语义投影相等；canonical XML 和对象计数仍记录为诊断，但不作为接受条件。

这个分层是 OneNote COM 复制语义的一部分，而不只是测试便利：`UpdatePageContent` 会重新生成或规范化 MathML namespace 序列化、`TagDef` index、列表序号状态、对象 ID、Outline/OE 分块和部分属性。若对所有内容统一使用严格 XML，会把成功复制误报为失败；若对整页统一放宽，又可能掩盖 Table/Image 等稳定结构的真实丢失。MathML tier 因此只替换完整公式 root 后比较页面其余 canonical 结构，并单独严格比较 content-free 公式语义投影；List/Tag 则继续使用自己的受限语义门。

Copy 输出规范化只针对 `DisplayEquation` 前已验证为纯空白的包装：在每次 `UpdatePageContent` 前移除紧邻 block MathML 的整个空白 span 及其中全部 `<br/>`，也移除同位置遗留的裸 `<br/>`。带可见文字、嵌套其他标签或不紧邻 display MathML 的 span/break 不会被清理。这样即使 OneNote COM 在写回时重新生成一个空白 span/break，链式 Copy 也保持有界，而不会继续从 `1 → 2 → 3` 累积。普通换行、行内公式及 MathML 外其他富文本不受影响。`page_results[].normalizations.redundant_breaks_before_display_mathml_removed` 与 `display_equation_empty_spans_removed` 分别记录清理的 break 和 span 数量。

`List/Tag/InkDrawing/UIShape/InsertedFile` 已是 validated/lossless 类型；InkDrawing 使用 `1e-4` 几何容差，UIShape 使用 `0.02`，InsertedFile 与 MediaFile 保持 strict canonical。Move 只消费生产 Copy 的 `copy_contract_satisfied`，不再次按类别或 `lossless` 分流；之后仍独立验证选定范围、源快照、非永久删除和排除后代保留。`FileAttachment` 与 `MeetingInfo` 仍保持 unverified。`Embedded Spreadsheet`（内嵌电子表格）是尚未取得公开 `kind`/XML 证据的产品能力类别，当前明确 unsupported，不属于 Copy fidelity 集合。

内容类型的 Copy/Move 验证不构成创建能力。当前没有为 `InsertedFile`、`InkDrawing`、`UIShape` 或 `MediaFile` 提供已验证的生产创建合同，也不宣称 `create_page`、`append_to_page` 或其他 typed mutation 能程序化生成这些原生对象。四种类型的已验证范围都是 reconstruction Copy，以及在共享 Copy 门通过后消费该结果的 Move；InsertedFile 的路径准备合同不属于程序化创建能力。

Online Video 当前不能满足 lossless Copy 合同。已观察到的 reconstruction 会保留预览图、可见文本、图片 binary 和外部链接，但丢失播放器绑定；它没有独立的公开 `kind` 或可证明播放器保真的 capability。Copy 必须 fail closed，Move 不得删除源。证据与环境边界见 [`lesson/online_video_copy_loses_player_semantics.md`](../lesson/online_video_copy_loses_player_semantics.md)。

D 阶段的交互证据已将 comparator 校准结果纳入生产 tier：`semantic_ink_drawing` 对 InkDrawing 子树结构、数据 hash 和 `Position/Size` 有界几何误差取证，自由墨迹使用 `1e-4` 绝对容差；`semantic_ui_shape` 复用相同的 Decimal 逐字段比较机制，但根据真实 Shape bounding-box 重算证据使用独立 `0.02` 绝对容差，并额外要求 source/target 的 `ShapeInfo` 及可选 `AnchorPoint` marker/子树一致。超过容差、非数字、结构或数据差异仍 fail closed。

Move 对选定范围内的每个源 Page 调用 `DeleteHierarchy(permanently=false)`。通用删除服务会有界回读：对象必须从活动 hierarchy 消失，或者明确回读为 `is_in_recycle_bin=true`；若仍处于活动树则失败。root-only Move 在此之前先提升被排除的后代，并在删除后确认它们仍活动且内容未变。全部选定 Page 与保留页通过对应关口后 Move 才成功；manual scenario 再以双 Notebook after snapshot 独立确认。COM 是否再次暴露旧 ID 及其回收站标记不是验收条件，因为实际 OneNote UI 可能已在“已删除的笔记”中显示页面，而 COM hierarchy 仍不返回对应对象。返回中的 `recycle_bin_verification=verified|not_required_com_unavailable`、`recycled_source_ids` 和 `recycle_unverified_source_ids` 只表达诊断置信度，不改变非永久删除与活动树缺失的成功语义。该限制的观察证据、错误验收模型和可复用结论见 [`lesson/onenote_com_recycle_bin_visibility.md`](../lesson/onenote_com_recycle_bin_visibility.md)。

## 8. Export、导航、同步与关闭

| 工具 | 参数 | 成功时的主要返回 |
| --- | --- | --- |
| `publish_object` | `object_id`, `target_path`, `format="pdf"`, `overwrite=false` | `item`, `path`, `format`；只支持 Notebook/Section/Page，要求 backend 返回同一解析后路径且精确目标文件实际存在。 |
| `navigate_to` | `object_id`, `page_content_object_id=""`, `new_window=false` | `item`, `navigated=true`。 |
| `navigate_to_url` | `url`, `new_window=false` | `navigated=true`。 |
| `get_hyperlink` | `object_id`, `page_content_object_id=""`, `web=false` | `item`, `hyperlink`。 |
| `sync_notebook` | `notebook_id` | `item`, `sync_requested=true`, `accepted=true`, `complete=false`, `completion_observable=false`, `converged=false`；COM 只证明请求提交，不声称同步完成。 |
| `close_notebook` | `notebook_id`, `expected_name`, `expected_modified=null` | 原 `item`, `final_state`, `closed=true`；要求写开关且不暴露 `force`。 |

导出格式：`one/onepkg/mhtml/mht/pdf/xps/word/doc/docx/emf/html/one2007`。Registry 将 Publish 分类为 `filesystem_effect`、Navigate 分类为 `ui_effect`、Sync/Close 分类为 `lifecycle`。`publish_object` 会写本地文件，但不修改 OneNote 对象；Navigate 只证明 action accepted，不声称持久状态改变。

## 9. 配置与默认 profile

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `LOCAL_ONENOTE_ENABLE_WRITES` | `false` | Create、Update、Rename、Reorder、Close。 |
| `LOCAL_ONENOTE_ENABLE_DELETES` | `false` | 层级和 Page 内容删除。 |
| `LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES` | `false` | 永久删除，不能替代 Delete 总开关。 |
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT` | `false` | 同 Notebook Page/Section/SectionGroup Reparent，不能替代写开关。 |
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION` | `false` | 同父级 Section Reorder，不能替代写开关。 |
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION_GROUP` | `false` | 仅供保留的内部诊断 service；不注册或授权任何生产 MCP Tool。 |
| `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY` | `false` | 四层 Copy；不能替代写开关。 |
| `LOCAL_ONENOTE_ENABLE_MOVE_PAGE` | `false` | Page Move；还要求 Writes、Deletes 和 Experimental Copy。Move 天然采用重建语义。 |
| `LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS` | `false` | 跨 Notebook Section/SectionGroup Move；还要求 Writes、Deletes 和 Experimental Copy，不替代 Page Move 开关。 |
| `LOCAL_ONENOTE_ENABLE_RAW_XML` | `false` | 仅供内部低层 service 的 raw mutation 授权；不改变生产 `tools/list`。 |
| `LOCAL_ONENOTE_MAX_SEARCH_PAGES` | `1000` | 过滤后、分页前的 OneNote index 候选 Page 上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_PAGE_CHARS` | `100000` | snippet hydration 的单 Page 处理字符上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_TOTAL_CHARS` | `2000000` | 当前调用、当前页 snippet hydration 的累计字符上限。 |
| `LOCAL_ONENOTE_MAX_SEARCH_SECONDS` | `30` | 从 `FindPages` 到当前页 snippet hydration 的总耗时上限；COM 调用使用不超过全局 bridge timeout 的剩余时间。 |
| `LOCAL_ONENOTE_MAX_SEARCH_SNIPPET_CHARS` | `400` | snippet 上限。 |
| `LOCAL_ONENOTE_MAX_COPY_RESOURCES` | `1000` | 单次 Copy 的层级对象上限。 |
| `LOCAL_ONENOTE_MAX_COPY_PAGES` | `200` | 单次 Copy 的 Page 上限。 |
| `LOCAL_ONENOTE_MAX_COPY_CONTENT_OBJECTS` | `10000` | 单次 Copy 的内容对象上限。 |
| `LOCAL_ONENOTE_MAX_COPY_PAGE_XML_BYTES` | `33554432` | 单 Page 完整 XML 字节上限。 |
| `LOCAL_ONENOTE_MAX_COPY_TOTAL_XML_BYTES` | `268435456` | 单次计划全部 Page XML 字节上限。 |
| `LOCAL_ONENOTE_MAX_COPY_PLAN_SECONDS` | `300` | 只读计划阶段秒数上限。 |
| `LOCAL_ONENOTE_MAX_COPY_EXECUTE_SECONDS` | `1800` | 执行阶段秒数上限；超限按部分失败报告。 |

生产 MCP 不注册 `update_page_xml/open_hierarchy/find_meta/merge_sections/set_filing_location`，也不存在 advanced profile。`delete_hierarchy` 与 `update_hierarchy_xml` 同样不属于任何生产注册路径；设置 Raw XML 开关不会枚举或恢复这些入口。内部 bridge operation `delete_hierarchy/update_hierarchy` 仅供受约束 typed service 使用，其他保留的低层方法只能由纯测试或明确诊断代码直接调用。逐项用途和安全边界见 [Advanced/低层操作](advanced_operations.md)。
