# OneNote 对象模型（P0/P1 实现版）

> 状态：实现契约
> 更新日期：2026-08-10
> 对应模型：`src/local_onenote_mcp/domain/`（由 `domain/__init__.py` 统一导出）
> 唯一层级解析入口：`src/local_onenote_mcp/hierarchy.py`

## 1. 边界与标识符

公开对象模型固定为 `Notebook → SectionGroup → Section → Page → PageContentObject`。层级对象以 OneNote COM `ID` 为唯一 mutation 主键；`path` 仅用于展示和 `resolve_identifier` 只读解析，不能授权写操作。

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
| `page_level` | integer | COM `pageLevel`，最小为 1。 |
| `order` | integer | 同 Section 完整 Page 序列中的零基位置。 |
| `parent_page_id` | string/null | 按完整有序 Page 序列和 `page_level` 推导。 |
| `has_children` | boolean | 是否存在缩进子 Page，派生值。 |

`parent_id` 表示 COM 容器父级；`parent_page_id` 表示 Page 缩进父级，两者不能混用。普通 List/Get 不读取正文。

### PageContentObject

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string/null | Page 内对象 ID；部分内嵌对象可能没有 ID。 |
| `page_id` | string | 所属 Page ID。 |
| `kind` | string | 如 `Outline/Image/InkDrawing/FileAttachment`。 |
| `parent_object_id` | string/null | Page XML 直接父对象 ID。 |
| `container_object_id` | string/null | 最近有 ID 的容器对象。 |
| `callback_id` | string/null | 二进制读取句柄。 |
| `media_type` | string/null | COM `format` 的规范化字段。 |
| `can_delete` | boolean | 当前对象能否被 COM 直接删除。 |
| `delete_target_id` | string/null | 可删除目标；子对象可能指向可删容器。 |

`get_binary_content` 会在当前 Page 的最新对象快照中再次校验 `callback_id`，不把它当作全局句柄。`delete_page_content` 同样要求对象仍存在且 `can_delete=true`。

## 4. 关系与树重建

- Notebook、SectionGroup、Section 的直接关系来自 XML 嵌套，`relationship_source=com`。
- Page 的 `order/page_level/parent_page_id/has_children` 需要同 Section 的完整有序列表，`relationship_source=derived`。
- `get_tree` 对容器使用 `parent_id`，对 Page 优先使用 `parent_page_id`；顶层 Page 挂到 `section_id`。
- 不完整层级片段不能冒充准确的 `page_count` 或 Page 缩进树。

## 5. Mutation 一致性

写操作只接收 ID，并要求调用者回传当前快照中的确认字段：

- 容器：`expected_name`、`expected_parent_id`，可选 `expected_modified`；
- Page：`expected_title`、`expected_section_id`，可选 `expected_modified`。

服务在调用 COM 前校验对象类型和确认字段，调用后按相同 ID 回读名称、父级、顺序或内容摘要。多步 `replace_page_body` 不是原子操作；发生中途失败时返回 `partial=true` 和 `completed_steps`。

## 6. 能力状态

| 能力 | 状态 |
| --- | --- |
| 四层 Create/List/Get、Page 内容读取和 typed 修改 | P0 已实现 |
| SectionGroup/Section/Page 回收站删除 | P0 已实现，默认关闭 |
| 显式范围正文搜索和硬预算 | P0 已实现 |
| Metadata Query、Path、Tree、Page 缩进树 | P1 已实现 |
| SectionGroup/Section Rename、Page Reorder | P1 已实现，默认关闭写入 |
| Section 同父级 Reorder | P1 typed 实验实现；由独立开关 fail closed，已有用户确认的真实 UI 排序证据 |
| SectionGroup 同父级 Reorder | 明确不支持并拒绝；后端仅提供按名称固定升序，不提供可变 sibling order |
| Section 同 Notebook Move | P1 实验实现；保持 Section ID；真实 COM 隔离验证前由独立开关禁用 |
| Reparent | 只表示同一 Notebook 内的容器换父级；Page、Section、SectionGroup 三类场景均已通过用户验收。Section 使用 typed `reparent_section`；Page/SectionGroup 当前仍是 [advanced raw-hierarchy 探针](advanced_operations.md#3-reparent-探针与产品能力边界)。 |
| Section 跨 Notebook 转移 | 不属于 Reparent；若未来交付，应作为 Move 新建 Copy→验证→非永久删除源合同并产生新 ID。 |
| 四层 Copy、Page Move | P2 实验实现；Move 天然采用 Copy→验证→非永久删除源的重建语义，仅在保真验证通过后处理源对象。 |
| Notebook/Section/Page Export、导航、Notebook Sync/Close | P1 typed 契约已实现 |
| Notebook Delete、SectionGroup Export | 不承诺 |
