# 公开 MCP Tool 契约

> 状态：当前实现态
> 更新日期：2026-08-17
> 权威来源：`src/local_onenote_mcp/tool_surface.py`、canonical Operation Registry 与公开 Tool schema

生产 MCP 只有一个 User profile，按用户任务固定公开 **53 个 Tool**。Tool 是否可见、是否获准执行、实现成熟度是三个独立维度：`tools/list` 只返回本页目录；所有 effect gate 默认关闭；内部能力不能通过环境变量重新注册。

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

Authorization 与平台前置条件是两个独立 Registry policy。所有需要七类公开授权之一的 operation，除恢复入口 `launch_onenote_gui` 外，均在授权成功后、协调 lease/cache generation/首个 backend call 之前要求 `onenote_gui_ready`。进程不存在、仅有后台进程、没有可见顶层窗口或 native probe 无法安全判定时 typed fail closed，且 `backend_calls=0`。纯 read 不受此 effect 前置条件限制。

## 2. 53 个公开 Tool

签名中的 `null` 表示可选值缺省。调用者不得用空字符串代替 optional value。所有对象参数均为精确 OneNote COM ID；名称、路径和 Query 结果只能用于发现候选，mutation 前必须固定 ID 并提交确认字段。

### Session（2）

| Tool | 参数 | 合同 |
| --- | --- | --- |
| `health_check` | 无 | 每个 MCP session 开始时调用；check-only，验证现有 `ONENOTE.EXE` 进程和可见窗口，绝不启动 GUI。成功时返回 53 项分类计数、7 个公开授权状态及运行时预算；执行授权 effect 前必须 ready。 |
| `launch_onenote_gui` | 无 | GUI 未 ready 时的显式恢复入口并豁免 readiness 前置条件。UI Control；已就绪时不启动。进程完全不存在时只解析受信任的注册目标并发出一次启动请求，再有界观察 GUI readiness；随后调用者必须再次 `health_check`，再重试原 effect。process-only、解析失败、启动异常与超时均 typed fail closed。 |

readiness 失败 envelope 的 `error.details.failed_precondition` 为 `onenote_gui_ready`，并给出 `health_check → launch_onenote_gui → health_check → retry_original_operation` 恢复顺序。若 `ui_control_enabled=false`，调用者需开启 `LOCAL_ONENOTE_ENABLE_UI_CONTROL=true` 后重启 MCP server，或由用户手动启动可见 OneNote Desktop GUI；Runtime 不会把 launch 隐藏进 read、effect、初始化或 `tools/list`。

### Hierarchy Browse（7）

| Tool | 参数 |
| --- | --- |
| `list_notebooks` | 无；列出当前打开 Notebook。 |
| `get_hierarchy_path` | `object_id`；返回 legacy display-only `path`、`ancestors/item` 与 additive `path_segments[{resource_type,id,name}]`。 |
| `expand_notebook` | `notebook_id`；递归到 Section 叶节点。 |
| `expand_section_group` | `section_group_id`；递归到 Section 叶节点。 |
| `expand_section` | `section_id`；返回完整 Page 缩进树。 |
| `expand_page` | `page_id`；返回该 Page 的完整缩进后代。 |
| `expand_hierarchy` | `root_id, max_depth=8, include_recycle_bin=false`；数值深度边界。 |

所有 Expand 返回统一递归 `tree={item,children[]}`，只读取 hierarchy metadata；target Notebook 内缺 ID、重复 ID、环、跨 Section 缩进父级、`page_level` 越出 1–3、Section 首个 Page 不是 level 1 或超过公共响应预算时明确失败。

L1 后跟随的 L3 直接映射为该 L1 的子节点：`parent_page_id` 为该 L1，Expand 树中位于该 L1 的 `children`，不虚构中间 L2。`query_page` 与 `expand_section` / `expand_page` / `expand_hierarchy` 共用这一派生。同 Notebook 另一 Section 的合法间隙不得阻断所请求 root。见 [UT-003](../todo/037_user_testing_experience_feedback_and_optimization.md)。

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
| `get_page_text` | `page_id, max_chars=60000, mode="rich"|"plain"`；`max_chars` 受进程级硬上限约束。默认 `rich` 返回 `{html, chars, mode, format="sanitized_html_v1", truncated}`；显式 `plain` 保留兼容的 `{text, chars}`。富投影保留受审查的强调、字体/颜色、链接、列表、Tag、表格和 canonical Presentation MathML 结构；OneNote 的完整 MathML 条件注释包装会被验证 namespace/元素/属性后规范为普通 `<math>`，其他 comment、可执行 markup、危险 URL 和不合约 MathML 均移除，不嵌入 binary payload。 |
| `get_page_content_objects` | `page_id`；以不嵌入 binary payload 的 `file_type` 快照返回 typed `PageContentObject` 清单以及可用的稳定删除/二进制读取 ID。 |
| `get_page_content_object_binary` | `page_id, page_content_object_id`；校验对象归属并受硬 binary 响应预算约束。 |
| `get_hyperlink` | `object_id, page_content_object_id=null, link_type="desktop"|"web"`。 |

Raw Page XML 不属于公开读取降级路线；`rich` 是由服务端构建的消毒投影，不是 XML、COM payload 或无界 HTML 透传。

### Create（4）

`create_notebook`、`create_section_group`、`create_section` 只需要 Create；`create_page` 会写入 title/初始正文，因此需要 Create + Writes。Create 与 Writes 均默认关闭。

| Tool | 参数 |
| --- | --- |
| `create_notebook` | `name, base_folder=null` |
| `create_section_group` | 单项：`parent_id, name`；批量：`parent_id, expected_parent_name, expected_parent_modified=null, items[1..20]`，每项 `{name}`。 |
| `create_section` | 单项：`parent_id, name`；批量：`parent_id, expected_parent_name, expected_parent_modified=null, items[1..20]`，每项 `{name}`。 |
| `create_page` | 单项：`section_id, title, content="", content_format="plain"`；批量：`section_id, expected_section_name, expected_section_modified=null, items[1..20]`，每项 `{title, content="", content_format="plain"}`。 |

`content_format` 支持当前实现验证的 plain/HTML/Markdown 路径。Create 只接受精确 parent ID，并通过 allocated ID 和 live read-back 收敛；Page batch 与单项模式一样允许重复标题，每项仍必须返回独立 fresh allocated ID，绝不按标题选择旧 Page。Section/SectionGroup batch 则拒绝规范化后的重复或已有直属名称碰撞。`LOCAL_ONENOTE_ENABLE_COPY` 不再是生产开关或兼容别名。

### Rename（3）、Reorder（3）、Organize（3）

Rename 与 Reorder 需要 Writes：

- `rename_page(page_id, title, expected_title, expected_section_id, expected_modified=null)`
- `rename_section_group(section_group_id, new_name, expected_name, expected_parent_id, expected_modified=null)`
- `rename_section(section_id, new_name, expected_name, expected_parent_id, expected_modified=null)`
- `reorder_page(page_id, expected_title, expected_section_id, after_page_id=null, page_level=0, expected_modified=null, include_subpages=false)`
- `reorder_section(section_id, expected_name, expected_parent_id, after_section_id=null, expected_modified=null)`
- `sort_children(parent_id, expected_parent_name, expected_child_ids, child_type=null, key="name", direction="ascending", expected_parent_modified=null)`

Organize 同时需要 Writes + Organize：

- `reparent_page(page_id, destination_section_id, expected_title, expected_section_id, expected_modified=null, include_subpages=false)`
- `reparent_section(section_id, destination_parent_id, expected_name, expected_parent_id, expected_modified=null)`
- `reparent_section_group(section_group_id, destination_parent_id, expected_name, expected_parent_id, expected_modified=null)`

所有公开 Page 范围统一为 `include_subpages: bool = false`，不接受旧 `page_scope` alias。`false` 只选择目标 Page；Delete/Reparent/Move/Reorder 会在主操作前将排除后代整体提升一级并验证保护拓扑。`true` 选择完整缩进子树；Reparent/Move/Reorder 保持块内顺序和相对层级，Delete 冻结完整 ID 范围并按叶到根非永久删除。Reparent 只在同一 Notebook 内改变父级；Section 与 SectionGroup 保持对象 ID，Page 在 OneNote 重映射时返回经验证的一对一 **Page ID** `id_map` 和最终对象。生产 Reparent、Page/Section Reorder 与 `sort_children` 的 read-back 只验证有界、稳定的 hierarchy（typed ID、父级、完整直接子序列、子树/缩进和 sibling order），不读取 Page 正文或推导内容对象 ID 映射；Page/Section Reparent 要求连续两次稳定 hierarchy 观测，SectionGroup Reparent 要求连续四次，二者使用相同的有界 deadline。容器 Reorder 与 Sort 响应以 `verification_scope.page_content="not_read"` 明示该边界。逐 Page 内容和内容对象保真比较仅由 human-gated manual-validation scenario 承担，不能解读为单次生产调用的正文验证。Reparent 不是 Copy 或跨 Notebook Move。SectionGroup reorder 没有稳定后端语义，因此不公开。

上述三个 Rename 和三个 Reparent 工具同时支持 `items[1..20]` 批量模式，工具名不变，也没有 `batch_*` 别名。批量项保持各单项工具的 exact ID、现有名称/标题、父级和可选 modified confirmation；Rename 每项再给出显式 `new_name`/`new_title`，Reparent 的所有项共用一个 destination，Page Reparent 项可各自选择 `include_subpages`。Page Reparent/Delete 从同一 mutation 前快照冻结整批 scope，并按受影响 Section 一次性完成所有必要提升；全部提升收敛前不会开始任何主操作。顶层单项 identity 字段与 `items` 互斥；所有批量目标必须同类型、位于同一 Notebook，且先整体通过重复、范围重叠、目标循环、名称碰撞与预算检查。Create、Rename、Reparent、Delete 全部在 item 调用完成后再次 live 读取整批最终 hierarchy，以输入 identity 返回 `final_hierarchy`；Page 正文始终为 `not_read`。最终整批对账失败时，即使各 item 已分别返回，也必须以 partial failure 和人工恢复指引结束。

Batch Mutation 的预算与 Copy 完全独立。`health_check.batch_mutation_budget` 投影五个正整数上限：`max_catalog_resources=100000`、`max_effective_resources=1000`、`max_effective_pages=200`、`max_direct_siblings=1000`、`max_page_content_chars=500000`，分别对应 `LOCAL_ONENOTE_MAX_BATCH_CATALOG_RESOURCES`、`LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_RESOURCES`、`LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_PAGES`、`LOCAL_ONENOTE_MAX_BATCH_DIRECT_SIBLINGS`、`LOCAL_ONENOTE_MAX_BATCH_PAGE_CONTENT_CHARS`。Catalog 上限约束一次 content-free exact-ID 定位读取；effective 上限只计算目标、选中/受保护后代、destination、confirmed parent 和请求新建项，Rename/Create 的直接兄弟冲突证据另行计数。Notebook 中其余 Page 不消耗 effective Page 预算。超限在 principal mutation 前以 `budget_dimension/observed_count/configured_limit/content_exposed=false` fail closed。

`sort_children` 只稳定排序完整、active、直接子序列，不接受 recursive 参数。`expected_child_ids` 接受 1–1000 个唯一 ID，并继续受当前 Notebook resource/Page 预算约束；它不复用 batch 的 20 项上限。子类型由父类型推断：Notebook 或 SectionGroup 只能排序其直属 Section；Section 或 Page 只能排序其直属 Page。可选 `child_type="section"|"page"` 仅作一致性断言，冲突即在 mutation 前拒绝。它不排序 SectionGroup；Notebook/SectionGroup 下的 SectionGroup 槽位保持不变。`key` 仅为 `name|created|modified`，`direction` 仅为 `ascending|descending`；同键保持原相对顺序，时间缺失或不可比较时整次 fail closed。Page 以直属 Page 及其完整缩进后代为不可拆分块移动，仅改变这些块的顺序，不递归排序块内后代。

所有批量模式都先对整个请求完成 live 预检，再按输入顺序逐项复用原单项 mutation；它们不是事务。首个失败或不确定结果会停止后续项，返回 `applied/failed/not_attempted` 逐项状态和人工恢复指引；partial 详情固定声明 `rollback_attempted=false`、`mutation_replayed=false`，不自动 rollback、重放或盲目重试。成功响应以输入序号对账，Create 另外返回每项新分配的精确 ID；一次整批最终回读再验证全部 Create identity、Rename 最终名称、Reparent 最终父级或 Delete inactive/recycle 状态。

### Page Content Mutation（4）

| Tool | 参数 | 授权 |
| --- | --- | --- |
| `append_page_content` | `page_id, content, expected_title, expected_section_id, expected_modified=null, content_format="plain", x=null, y=null` | Writes |
| `add_page_image_from_file` | `page_id, image_path, expected_title, expected_section_id, expected_modified=null, x=36, y=120, width=null, height=null` | Writes + Local File IO |
| `replace_page_body` | `page_id, content, expected_title, expected_section_id, expected_modified=null, content_format="plain"` | Writes + Deletes |
| `delete_page_content_object` | `page_id, page_content_object_id, expected_title, expected_section_id, expected_modified=null` | Deletes |

图片格式从文件 magic bytes 推断，并与扩展名交叉验证；调用者不能指定 `image_format`。`replace_page_body` 不修改 title，是可能返回 partial/reconciliation 的多步 saga。内容对象删除 ID 必须来自同一 Page 的 typed object list，并且该对象被标记为可删除。

当前公开写入面不提供 OneNote 修订历史、track-changes、revision ID 选择/回退或任意文本范围 patch。调用者只能使用上述 append、整正文 replace、精确内容对象 delete 和图片添加操作；`expected_modified` 只用于 mutation 前的乐观并发确认，不代表可寻址或可恢复的修订版本。

### Recoverable Delete（3）

`delete_page(include_subpages=false)`、`delete_section`、`delete_section_group` 均需要 Deletes 和 exact-ID confirmation，并固定执行非永久、可恢复删除。Page Delete 可选择仅删除根并保护后代，或删除完整子树。每个原工具也支持与其类型一致的 `items[1..20]` 批量模式；Page batch item 可独立给出 `include_subpages`。顶层单项字段与 `items` 互斥，请求整体预检并拒绝重复、回收站对象以及祖先/后代范围重叠。公开 schema 没有 `permanently`；永久删除工具当前不存在。

### Copy（4）与 Reconstructive Move（3）

Copy 需要 Create + Writes；Move 需要 Create + Writes + Deletes。不存在独立 Copy gate。

Copy/Move 是内容与拓扑级重建，不继承 OneNote-owned 修订/审计元数据。Page 转换会排除 source authorship/revision marker 以及 `creationTime`、`dateTime`、`lastModifiedTime` 等 volatile 时间字段；Section、SectionGroup、Notebook 目标也以新建对象的时间元数据为准。OneNote 写回后可以生成目标自己的 marker/时间值，但不承诺与 source 相同。`verified`、`lossless` 与 `copy_contract_satisfied` 仅覆盖下述受支持标题、内容对象和拓扑投影，不表示 source revision marker、原始创建时间或原始修改时间已保真。产品层非承诺见[产品能力边界](../product/README.md)；未来重新评估时间保真的开放方向见 [TODO 043](../todo/043_copy_move_source_timestamp_fidelity.md)。

- `copy_page(page_id, destination_section_id, expected_title, expected_section_id, expected_modified=null, destination_title=null, include_subpages=false)`
- `copy_section(section_id, destination_parent_id, expected_name, expected_parent_id, expected_modified=null, destination_name=null)`
- `copy_section_group(section_group_id, destination_parent_id, expected_name, expected_parent_id, expected_modified=null, destination_name=null)`
- `copy_notebook(notebook_id, expected_name, expected_modified=null, destination_name=null, destination_base_folder=null)`
- `move_page(...)` 与 Page Copy 参数同构
- `move_section(...)` 与 Section Copy 参数同构
- `move_section_group(...)` 与 SectionGroup Copy 参数同构

七个操作都是单次调用：Runtime 内部从 live source/destination 建立计划、执行预算检查、复制、验证并返回新 ID 映射；不接受 `plan_digest` 或 planning token。Page root 的逻辑 title 与容器 filesystem leaf name 分离：默认值和显式 `destination_title` 都精确保留 `/`、`\\`、`:`、`~`、`%`、重复空格和 Unicode，只做非空/控制字符合法性检查；Notebook、SectionGroup、Section 仍使用 filesystem-safe leaf。Page 创建/Copy 优先验证 allocated exact ID、Page 类型、exact Section ID 与 exact title；只有 allocated ID 消失时，才接受“同 exact Section + exact title + 本次新出现”的唯一 fresh ID remap，绝不解析扁平 display path。

Page fidelity 按内容能力选择验证：既有 MathML、DisplayEquation、List/Tag、Ink/UIShape 档保持独立；完整投影的纯 RichText Page 和含受支持 List/Tag/Table/Image 的 Page 使用 `semantic_content_v1`，分别验证有效 title、富文本样式/链接、List/Tag、表格行列与单元格语义、非空 Outline、对象类型和 binary hash。该投影比较每个非空白字符的有效 inline 格式，而不是 OneNote 可合并的冗余嵌套 `span` 包装；OneNote 写回把边界空白从一个相邻格式 run 移入另一个时，空白使用中性 style 后再合并，完整文本序列仍须精确相等，任何非空白字符的 style/link 归属变化继续失败。Title 文本节点合并、空 Outline 消除与表格 Cell 内 OE 扁平化仍按既有窄规则处理。Table `Column.width` 仅在 transformed→target 回读允许 `abs(target-expected)/abs(expected) <= 0.05`；两端都必须为有限正 Decimal，边界 5% 通过，缺失/零/负数/NaN/Infinity/非数值均 fail closed。source→transformed width、Table 拓扑/其他属性及全部其他语义字段仍精确。

`semantic_display_equation` 的 transformed→target comparator 另有一个 pairwise Title header 规则：只有 exact title 文本相等、Title 结构可识别、Title OE 数量相等且每对 OE 的 canonical 属性名集合相同时，才把 `alignment`、`quickStyleIndex`、`style` 的值视为 COM 派生表示。这里的 canonical 集合复用全局 comparator 已有的 generated/volatile exclusion；例如 transformed payload 不含而回读重新生成的 `objectID` 不会单独阻止该规则，也不会扩大现有忽略集合。有效属性增删、其他属性、Title 文本/结构、正文 OE style、MathML 和其余 Page canonical 仍严格比较。响应中的 `title_oe_com_style_normalization` 只给出 applicable/applied、计数和属性名，不返回 style 值或 Page 内容；Title 结构失败稳定分类为 `PageTitle/page_title_structure_mismatch`。

所有 verification tier 的 `page_results[*].title_readback_stages` 都独立返回 content-free 的 source→transformed 与 transformed→target title check。Plan 显式记录调用者是否请求 `destination_title`，不得用转换后的标题是否不同反推意图；默认模式要求 source XML title 与 source metadata 一致且 transformed title 原样保留，显式模式要求 transformed title 等于请求值，最终 target XML title 必须精确等于 transformed title。任一 stage 失败都会使该 Page `lossless=false`，进而阻止 Copy contract 和 Move 删源。`semantic_content_stages` 继续只属于 `semantic_content_v1` 的完整内容投影，不被扩展到 DisplayEquation 等独立 tier。

`page_results[*].semantic_content_stages` 返回 content-free 的 source→transformed 与 transformed→target 完整性、check、摘要 hash 和有界 mismatch path。每个 equivalence 还 additive 返回排序去重的 `failed_content_object_types`、最多 24 条稳定 `content_object_failures` 及 `{limit,reported,truncated,total}`；Table width 失败包含 ordinal、阈值和相对 delta，但不返回标题、正文、style 值、raw XML 或 binary。无法分类或投影不完整分别以 `semantic_mismatch_unclassified` / `semantic_projection_incomplete` fail closed。

当 Copy 已创建目标但 Page equivalence 不一致时，service 按 `failed_content_object_types` 抛出 `PageReadbackMismatch` 的 content-category 子类：单一 `PageTitle`、`RichText`、`List`、`Tag`、`Table`、`Outline`、`Image`、`InsertedFile`、`FileAttachment`、`MediaFile`、`DisplayEquation`、`InkDrawing` 或 `UIShape` 分别使用对应 typed exception；多个类别使用 `PageMixedContentReadbackMismatch`，空或无法识别的类别使用 `PageUnknownContentReadbackMismatch`。异常 details 固定包含排序去重的 `failed_content_object_types`、`readback_content_category`、稳定的 `readback_error_code` 与 `content_exposed=false`。Move 把 outcome 收敛为 `copy_only` 时必须保留该异常子类。为兼容既有安全响应合同，MCP 顶层 code 仍为 `partial_failure`，具体异常类写入 `details.error_type`；typed exception 不改变 `failed_step=verify_copy`、目标留存、源页不删除或人工恢复要求。

Move 只有在 Copy 已验证且源状态重验通过后才执行源对象的非永久删除；typed diagnostics 绝不放宽删源门。partial/indeterminate 不自动重放。Page 默认为单页，容器始终递归。

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
| Create | `LOCAL_ONENOTE_ENABLE_CREATE` | Notebook/SectionGroup/Section；Page Create 及 Copy/Move 的目标创建阶段 |
| Writes | `LOCAL_ONENOTE_ENABLE_WRITES` | Page Create 初始内容、Rename、Reorder、Append 及 Copy/Move 写入阶段 |
| Deletes | `LOCAL_ONENOTE_ENABLE_DELETES` | 内容对象删除、可恢复删除、Replace 删除阶段、Move 源删除 |
| Organize | `LOCAL_ONENOTE_ENABLE_ORGANIZE` | `reparent_*`，并要求 Writes |
| Local File IO | `LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO` | 从文件加图、导出 PDF |
| UI Control | `LOCAL_ONENOTE_ENABLE_UI_CONTROL` | GUI launch、typed navigation |
| Notebook Lifecycle | `LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE` | Sync request、Close |

授权在 Operation Runtime 的 authorizer 阶段、取得协调 lease 和调用 handler 之前执行；Service 再做纵深门控。旧 experimental/move 环境变量不再授予任何能力。内部 `Permanent Deletes` 与 `Raw XML` 防线不属于公开 53 项授权面，也不创建 Tool。

## 4. 非公开能力

`resolve_identifier`、`get_page_xml`、`navigate_to_url`、`get_special_locations`、`get_parent` 保留在非注册 Internal & Incubating catalog；它们与 User profile 不相交，也没有批量 exposure 开关。`reorder_section_group`、任意 raw hierarchy/page mutation、generic open/find/merge/filing、所有 Plan/Preview Tool 均禁止进入生产 MCP。

完整机器投影与 promotion requirements 见 `src/local_onenote_mcp/tool_surface.py`。对象语义见 [对象模型](object_model.md)，执行阶段与审计见 [Operation Runtime](operation_runtime.md)，低层边界见 [内部低层与诊断操作](advanced_operations.md)。
