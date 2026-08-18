# OneNote 对象模型

> 状态：实现契约
> 更新日期：2026-08-16
> 对应模型：`src/local_onenote_mcp/domain/`（由 `domain/__init__.py` 统一导出）
> 唯一层级解析入口：`src/local_onenote_mcp/hierarchy.py`

## 1. 边界与标识符

公开对象模型固定为 `Notebook → SectionGroup → Section → Page → PageContentObject`。层级对象以 OneNote COM `ID` 为唯一 mutation 主键；`path` 仅用于展示，不能授权写操作。发现从 List、Query 或 Search 开始，再固定 exact ID。

COM XML 中未列入本文的 attribute 不进入公开返回。字段缺失时返回 `null`，不依据动作能力推测状态。所有时间字段保留 COM 返回的 ISO 字符串；服务不改写时区。

## 2. 公共层级字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `resource_type` | enum | `notebook/section_group/section/page`。 |
| `id` | string | COM 对象 ID；写操作唯一主键。 |
| `name` | string | 仅 Notebook、SectionGroup、Section 使用。Page 使用 `title`。 |
| `path` | string | 按当前祖先名称派生的显示路径，不是稳定主键。 |
| `parent_id` | string/null | COM 层级中的直接父对象；Notebook 为 `null`。 |
| `depth` | integer | 容器树深度；不表示 Page 缩进。 |
| `created` | string/null | `dateTime/createdTime` 白名单映射。 |
| `modified` | string/null | `lastModifiedTime` 白名单映射。 |
| `is_in_recycle_bin` | boolean | 来自回收站 attribute 或回收站祖先路径。 |
| `relationship_source` | enum | `com` 或 `derived`。Page 缩进关系为 `derived`。 |

## 3. 各类型静态字段

### Notebook

公共字段之外包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `section_group_ids` | string[] | 直属 SectionGroup ID。 |
| `section_ids` | string[] | 直属 Section ID，不含组内后代。 |
| `is_open` | boolean/null | 仅 COM 明确给出 `isClosed` 时映射；否则 `null`。 |

Notebook 不支持 Delete；`close_notebook` 不是删除。

### SectionGroup

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `notebook_id` | string/null | 所属 Notebook ID。 |
| `parent_section_group_id` | string/null | 直接父组；Notebook 直属时为 `null`。 |
| `section_group_ids` | string[] | 直属子组 ID。 |
| `section_ids` | string[] | 直属 Section ID。 |

### Section

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `notebook_id` | string/null | 所属 Notebook ID。 |
| `parent_section_group_id` | string/null | Notebook 直属时为 `null`。 |
| `page_count` | integer/null | 基于完整层级快照计算的 Page 数。 |
| `is_locked` | boolean/null | 仅 XML 明确给出 `locked` 时返回。 |
| `is_read_only` | boolean/null | 仅 XML 明确给出 `isReadOnly` 时返回。 |

### Page

Page 不公开 `name`，统一使用 `title`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | string | Page 标题。 |
| `notebook_id` | string/null | 所属 Notebook ID。 |
| `section_id` | string/null | 所属 Section ID。 |
| `page_level` | integer | COM `pageLevel`；OneNote Desktop 的合法范围为 1-3（根 Page 加两级 Subpage）。 |
| `order` | integer | 同 Section 完整 Page 序列中的零基位置。 |
| `parent_page_id` | string/null | 按完整有序 Page 序列和 `page_level` 推导：同 Section 上最近的更浅祖先；L1 后跟随的 L3 的父级就是该 L1。不是 COM 容器父级。 |
| `has_children` | boolean | 是否存在缩进子 Page，派生值。 |

`parent_id` 表示 COM 容器父级；`parent_page_id` 表示 Page 缩进父级，两者不能混用。普通 List/Get 不读取正文。Microsoft 的 OneNote Desktop 支持文档明确说明只能有两级 Subpage，因此真实 fixture 和 mutation 验证不得构造 `page_level=4`；参见 [Create a subpage in OneNote](https://support.microsoft.com/en-US/OneNote/onenote-help-and-learning/create-a-subpage-in-onenote)。

相邻 `page_level` 不必连续。真实 Notebook 可以出现 `page_level=1` 后直接 `page_level=3`；这仍是合法 COM `pageLevel` 序列。当前映射是：L1 后跟随的 L3 **直接成为该 L1 的子节点**（`parent_page_id` = 该 L1，Expand 树中位于该 L1 的 `children`，不虚构中间 L2）。紧随的连续 L3 同样是该 L1 的直接子节点。`query_page`、`expand_section`、`expand_page` 与 `expand_hierarchy` 共用该派生规则。用户测试记录见 [UT-003](../todo/037_user_testing_experience_feedback_and_optimization.md)。

### PageContentObject

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string/null | Page 内对象 ID。优先使用 COM `objectID`；没有 `objectID` 但具有 `callbackID` 的二进制叶对象使用后者作为 Page-scoped ID；两者均缺失时为 null。 |
| `page_id` | string | 所属 Page ID。 |
| `kind` | string | 如 `Outline/Image/InkDrawing/FileAttachment`。 |
| `parent_object_id` | string/null | Page XML 直接父对象 ID。 |
| `container_object_id` | string/null | 最近有 ID 的容器对象。 |
| `callback_id` | string/null | 二进制读取句柄。 |
| `media_type` | string/null | COM `format` 的规范化字段。 |
| `can_delete` | boolean | 当前对象能否被 COM 直接删除。 |
| `delete_target_id` | string/null | 可删除目标；子对象可能指向可删容器。 |

公开 Page 对象只使用 `kind`。Page XML parser 为 element local name 使用的内部中间字段 `type` 不属于公开合同；domain mapper 会把它映射到 `PageContentObject.kind`。调用方、manual-validation snapshot、detector 和 comparator 不得接受 `type` fallback，缺少 `kind` 或公开形状中残留 `type` 时应 fail closed。

OneNote UI Shape 当前没有独立的公开 `kind=Shape`。2026-08-11 的矩形与箭头真实回读都得到 `kind=InkDrawing`，但其 XML 子树共同含 `ShapeInfo`；箭头另含 `AnchorPoint`。因此 `UIShape` 只是 content-free capability projection 的复合分类：它要求公开对象仍为 `InkDrawing` 且结构 marker 完整，用来与普通自由墨迹严格区分，不是新增或伪造的 `PageContentObject.kind`。

`DisplayEquation` 同样是 Page 语义 capability/content type，不是公开 `PageContentObject.kind`。它只在一个完整、有界的 Presentation MathML root 明确带有 `display="block"` 时由 Page XML 投影产生，Copy 用它选择单行公式专属的输出规范化和读回 comparator。无 `display` 属性的行内公式继续属于 RichText；未知、残缺或不合约 MathML 不得通过 `DisplayEquation` 分类绕过 fail-closed 比较。`get_page_text(mode="rich")` 会把 OneNote 完整的 MathML conditional-comment wrapper 验证并规范化为不带 prefix 的 canonical `<math xmlns="http://www.w3.org/1998/Math/MathML">`；普通 comment、错误 namespace、额外属性或未知 MathML 元素不会进入公开投影。COM 初次生成和后续重建都可能增加公式前空白包装，该限制并非 Copy 独有；证据边界见 [`lesson/display_equation_com_leading_whitespace_normalization.md`](../lesson/display_equation_com_leading_whitespace_normalization.md)。

OneNote“插入 → 录制音频”和“插入 → 录制视频”在当前实测环境都公开一个 `kind=MediaFile`。Page XML 还会包含 `MediaPlaylist/MediaReference`，以及同一含 MediaFile 的 Outline 中的 `OE/MediaIndex/MediaReference` 和 `OE/MediaFile/MediaReference`；Template materialize 后，媒体时间轴可能规范化为只含 `MediaIndex + T` 的 OE，T 使用单一 `span` 富文本。它们都是媒体支撑而不是额外的公开 PageContentObject kind；projection 和 Copy 转换只在精确媒体关联结构内接受节点/时间轴 span，普通 RichText 仍保持独立能力。录像 v8 bootstrap 与 materialized live validation 均未观察到额外 unknown/unsupported 节点。

`FileAttachment` 与 `InsertedFile` 是不同的 XML element local name 和不同的公开 `kind`，不得互设别名。2026-08-11 在一个 OneNote `16.0.20228.20158`、Office x64 环境中，菜单“插入 → 文件附件”的三份机器回读都只观察到 `InsertedFile`；这意味着该环境没有通过此 UI 路径观察到独立 `FileAttachment`，不改变跨版本对象模型。证据边界和环境详情见 [`lesson/onenote_page_object_kind_and_file_attachment_representation.md`](../lesson/onenote_page_object_kind_and_file_attachment_representation.md)。

`Embedded Spreadsheet`（内嵌电子表格）目前只是产品能力类别，不是已观察到的公开对象模型枚举。项目尚未收集它的 `PageContentObject.kind`、Page XML 或引用边界，因此不得把它建模为 `Table`、`InsertedFile`、`FileAttachment` 或猜测的 Office/OLE kind。当前支持状态明确为 unsupported；未知或未验证表示由 Copy 合同 fail closed。证据边界见 [`lesson/copy_content_type_exclusions.md`](../lesson/copy_content_type_exclusions.md)。

`get_page_content_objects` 与二进制读取的归属复核使用 `page_info=file_type`，使 COM 返回文件类型和 `callbackID`、但不把 Base64 payload 嵌入 Page XML。OneNote XML 既可能把 `callbackID` 作为内容元素属性，也可能使用直接子节点 `<CallbackID callbackID="…"/>`；两种表示映射为同一个内部 `callback_id`，元数据子节点本身不成为公开内容对象。`get_page_content_object_binary` 的公开参数 `page_content_object_id` 指向上述对象 `id`；Service 会在当前 Page 的最新对象快照中重新确认归属，再使用对象的内部 `callback_id` 读取二进制。对象同时具有 `objectID/callbackID` 时两者保持分离并完成转换；只有 `callbackID` 的二进制叶对象把该 OneNote ID 作为 Page-scoped fallback，仍必须连同精确 `page_id` 重新定位，不能作为全局句柄。`delete_page_content_object` 同样要求对象仍存在且 `can_delete=true`。

## 4. 关系与树重建

`path` 是面向显示和兼容只读发现的 friendly 字段，不是唯一键。同一 Section 中多个 Page 可以拥有相同 title/path；mutation 目标必须使用 exact ID。创建回读只在 COM allocated ID 不可见且同路径恰有一个新出现、type/parent 均正确的候选时接受 remap，重复 path 必须 fail closed。

- Notebook、SectionGroup、Section 的直接关系来自 XML 嵌套，`relationship_source=com`。
- Page 的 `order/page_level/parent_page_id/has_children` 需要同 Section 的完整有序列表，`relationship_source=derived`。
- `list_notebooks` 是 OneNote 无真实 root 对象时的 open-only root discovery；它不伪造 COM root。
- 五个 Expand 共用一份关系图与 tree builder：容器使用 `parent_id`，Page 优先使用 `parent_page_id`，顶层 Page 挂到 `section_id`。
- `expand_notebook/expand_section_group` 在 Section 停止；`expand_section/expand_page` 返回完整 Page 缩进子树；`expand_hierarchy` 施加数值深度边界。
- 缺 ID、重复 ID、环、跨 Section 缩进父级、`page_level` 越出 1–3、或 Section 首个 Page 不是 level 1，不能冒充准确的 tree；超过公共响应边界时明确失败。
- 相邻 `page_level` 间隙（如 1 后直接 3）不是“不完整关系”。当前实现把该 L3 直接映射为前序 L1 的子节点，见 [UT-003](../todo/037_user_testing_experience_feedback_and_optimization.md)。

## 5. Mutation 一致性

写操作只接收 ID，并要求调用者回传当前快照中的确认字段：

- 容器：`expected_name`、`expected_parent_id`，可选 `expected_modified`；
- Page：`expected_title`、`expected_section_id`，可选 `expected_modified`。

服务在调用 COM 前校验对象类型和确认字段，调用后按相同 ID 回读名称、父级、顺序或内容摘要。多步 `replace_page_body` 不是原子操作；发生中途失败时返回 `partial=true` 和 `completed_steps`。

## 6. 能力状态

| 能力 | 状态 |
| --- | --- |
| 四层 Create/Get、Notebook root List、typed/通用 Expand、Page 内容读取和 typed 修改 | P0/P1 已实现 |
| SectionGroup/Section/Page 回收站删除 | P0 已实现，默认关闭 |
| typed/全部已打开 Notebook 正文搜索和调用级硬预算 | P0 已实现 |
| Metadata Query、Path、共享 Expand tree、Page 缩进树 | P1 已实现 |
| SectionGroup/Section Rename、Page Reorder | P1 已实现，默认关闭写入 |
| Section 同父级 Reorder | 已实现；使用 Writes gate，已有用户确认的真实 UI 排序证据 |
| SectionGroup 同父级 Reorder | 明确不支持并拒绝；后端仅提供按名称固定升序，不提供可变 sibling order |
| Section 同 Notebook 换父级（历史 Move 语义） | 已收敛为 typed `reparent_section`；保持 Section ID，由 Writes + Organize fail closed，已有用户确认的真实 COM 证据 |
| Reparent | 只表示同一 Notebook 内的容器换父级；公开 typed `reparent_page`、`reparent_section`、`reparent_section_group` 共用 Writes + Organize。Page 默认 `include_subpages=false`，只迁移选中对象并提升被排除后代；设为 `true` 时迁移完整缩进子树。生产 read-back 仅验证 hierarchy，不读取 Page 正文；逐 Page 正文/内容对象比较只在 human-gated manual validation 中保留。生产 MCP 不暴露 raw hierarchy XML。 |
| Section/SectionGroup 跨 Notebook转移 | 重建式 Move；完整子树 Copy 与验证后只对源容器根执行一次非永久删除，全部后代获得新 ID；同 Notebook 请求 fail closed。需要 Create + Writes + Deletes。 |
| 四层 Copy、Page/容器 Move | 已实现为单次公开调用；Page Copy/Move 用布尔 `include_subpages` 选择根 Page 或完整缩进子树，容器始终递归。内部 live planning 不暴露 token；Move 采用 Copy→验证→非永久删除源的重建语义。 |
| Notebook/Section/Page Export、导航、Notebook Sync/Close | P1 typed 契约已实现 |
| Notebook Delete、SectionGroup Export | 不承诺 |
