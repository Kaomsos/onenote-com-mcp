# Local OneNote MCP 对象模型审计与重构建议

> 审计对象：`local-onenote-mcp` commit `42092e5`  
> 审计日期：2026-08-04  
> 方法：静态检查 MCP 工具、COM bridge、XML 解析器与 Mock 测试；未操作真实 OneNote 数据。

## 核心阅读入口

本报告刻意维护两套不能混写的模型：

1. [当前对象—操作矩阵（源码事实）](#4-当前对象操作矩阵源码事实)：只记录参考项目当前公开 MCP 实际交付的 typed、generic 或 raw 能力；
2. [建议的目标对象—操作矩阵](#6-建议的目标对象操作矩阵)：记录采用更完备四层对象模型后可以追求的产品形态，并使用 `P0/P1/P2/V/X` 表示优先级或验证状态。

阅读或维护本文时，不得用第二张矩阵反向证明第一张矩阵已经实现，也不得因为底层 COM 方法或 raw XML 可以表达某种变化，就把它写成稳定对象操作。

## 1. 结论先行

这个项目已经证明了一件很有价值的事：Windows OneNote COM 足以支撑一个无需 Azure、OAuth 和公网请求的快速自用 MCP。它有 36 个公开工具，覆盖四层层级发现、正文读取与搜索、四层创建、Page 内容修改、层级删除、导出、导航、同步和 Section 合并；PowerShell bridge 也避免了把用户输入直接插值进脚本。

但它目前更像“COM 方法工具箱”，还不是一个边界稳定、对象语义统一的 OneNote 产品模型。核心问题不是能力少，而是能力组织方式不够整齐：

- 四层对象被压成同一种扁平 `HierarchyItem`，Notebook、SectionGroup、Section、Page 没有各自稳定的静态字段契约；
- typed tool、通用 hierarchy tool 和 raw XML tool 同时暴露，Agent 可以绕过上层语义直接写 XML；
- 同一参数可接受 ID、路径或唯一名称，方便人工调用，却让自动化的消歧、确认和幂等性变弱；
- README 所称的“Full CRUD”比源码实际保证更宽，尤其 Notebook 删除、对象更新、Copy/Move 和删除安全；
- Page 正文 Search 已经比 Graph 路线更有产品价值，但本地扫描只限制返回结果数，没有限制候选 Page 数、总读取字节数或总耗时；
- 写入和删除没有服务级开关、确认信息或修改时间前置条件，`permanently`、`force` 和 raw XML 对自用产品仍然过于锋利。

建议保留 COM-first 路线和已有底层能力，但把公开 MCP 重构为“对象模型优先，COM adapter 居后”：先定义 Notebook、SectionGroup、Section、Page、PageContentObject 的静态字段，再用统一的对象—操作矩阵决定哪些 typed operation 可以交付。raw XML 应退出默认工具面，只保留在明确启用的诊断/开发接口中。

## 2. 证据范围与结构判断

主要证据：

- [server.py](../../src/local_onenote_mcp/server.py)：36 个公开 MCP 工具、标识符解析、搜索和业务返回；
- [bridge.py](../../src/local_onenote_mcp/bridge.py)：20 个固定 PowerShell/COM 操作，以及每次调用创建 PowerShell 进程和 `OneNote.Application` 的执行方式；
- [xml_utils.py](../../src/local_onenote_mcp/xml_utils.py)：层级扁平化、Page 文本/内容对象解析、HTML/Markdown 到 OneNote XML 的转换；
- [constants.py](../../src/local_onenote_mcp/constants.py)：层级 Scope、创建类型、PageInfo、Publish 格式等枚举；
- [test_server.py](../../tests/test_server.py) 与 [test_xml_utils.py](../../tests/test_xml_utils.py)：当前 Mock 实际覆盖范围；
- [README](../../README.md)：项目对外宣称的产品能力。

当前真实架构是：

```text
MCP tool
  → Python 参数处理 / hierarchy 扫描 / XML 构造
  → 每次调用启动 powershell.exe
  → 从临时 JSON 读取参数
  → 创建 OneNote.Application COM 对象
  → 执行固定 bridge operation
  → 临时 JSON 返回
```

它的优点是 bridge 操作白名单固定、没有 PowerShell 字符串插值；代价是每次 COM 调用都有进程与 COM 初始化开销。README 的“high-performance”目前没有基准测试或长驻 broker 设计作为证据。

## 3. 参考项目实际对象模型

### 3.1 实际层级模型：一个扁平类型，而不是四个领域对象

`parse_hierarchy` 把 COM XML 中的 `Notebook`、`SectionGroup`、`Section`、`Page` 全部转换成同一种 `HierarchyItem`：

| 当前字段 | 来源 | 当前语义与问题 |
| --- | --- | --- |
| `type` | XML tag 映射 | 值为 `notebook`、`section_group`、`section`、`page`；这是唯一显式区分四层对象的字段。 |
| `id` | XML `ID` | COM 对象 ID；公开工具也允许不用 ID。 |
| `name` | XML `name` 或 `nickname` | Page 标题与容器名称被统一成 `name`，没有对象级命名差异。 |
| `path` | 本地按祖先名称拼接 | 便于人工定位，但它是派生显示路径，不是稳定主键。 |
| `level` | 本地递归深度 | 表示整个 Notebook 树中的深度，不等同于 Page 的缩进层级。 |
| `parent_id`、`parent_name` | 本地遍历 | 直接父节点；没有类型化父关系字段。 |
| `notebook_name`、`section_name` | 本地遍历 | 只保留名称，没有稳定的 `notebook_id`、`section_id`。 |
| 其他 XML attributes | 原样展开 | 返回字段随 COM XML 变化，缺少白名单、类型转换和稳定版本契约。 |

这意味着当前没有真正独立的 `NotebookModel`、`SectionGroupModel`、`SectionModel` 或 `PageMetadataModel`。`list_hierarchy`、`resolve_identifier` 和大部分 typed list 都返回同一类字典。

### 3.2 实际 Page 内容模型

Page 比其他对象多一层内容解析：

- `get_page` 返回 hierarchy item、解析后的 `title`、纯文本 `text`、内容对象 `objects`，并可选择返回 raw XML；
- `get_page_text` 只提取所有 `one:T` 的可见文本；
- `get_page_objects` 返回 `Outline`、`Image`、`InkDrawing`、`FileAttachment`、`InsertedFile`、`MediaFile` 等内容节点；
- 内容对象字段包括 `type`、`object_id`、`container_object_id`、`parent_object_id`、`delete_supported`、`delete_object_id`、`callback_id`、`format`；
- `get_binary_content` 根据 `callback_id` 返回 Base64。

这是有用的雏形，但 Page 元数据、Page 正文和 Page 内容对象仍混在工具返回中，没有明确的“默认元数据不带正文”和“大对象必须显式读取”边界。

### 3.3 实际标识符模型

公开工具普遍接受 `ID → 精确路径 → 唯一名称` 三段式解析。它提升了交互便利性，但同时造成：

- 写操作调用前可能扫描完整 hierarchy；
- 名称或路径变化会改变调用结果；
- 相同名称必须依赖运行时歧义错误；
- 删除与更新没有强制要求调用者先解析并回传精确对象确认信息。

建议保留 `resolve_identifier` 作为交互辅助，但所有 mutation 的稳定契约只接受对象 ID，并附带 `expected_name`、`expected_parent_id`、`expected_modified` 中适用的确认字段。

## 4. 当前对象—操作矩阵（源码事实）

这是本报告最重要的现状表。它描述公开 MCP 的实际交付，不把 COM 理论能力或 raw XML 可能性写成 typed tool 已实现。

状态：

| 标记 | 含义 |
| --- | --- |
| `M` | 已有对象语义相对明确的公开工具 |
| `M*` | 工具已公开，但完整性、安全或对象语义存在明显缺口 |
| `L` | 只能通过 generic hierarchy/raw XML 低层接口获得，尚无稳定对象工具 |
| `—` | 当前没有对应公开能力 |
| `X` | COM 本身不支持该对象语义，或当前声明与 COM 边界冲突 |

| 类别 | 操作 | Notebook | SectionGroup | Section | Page |
| --- | --- | --- | --- | --- | --- |
| `C` | 创建 | `M`：`create_notebook` | `M`：`create_section_group` | `M`：Notebook/SectionGroup 下创建 | `M`：`create_page` |
| `R` | 列出 | `M`：`list_notebooks` | `L`：只有 `list_hierarchy` 过滤 | `M*`：`list_sections` 的 Notebook 范围包含后代，未区分直接父级 | `M`：`list_pages` |
| `R` | 获取元数据 | `L`：`resolve_identifier` | `L`：`resolve_identifier` | `L`：`resolve_identifier` | `M*`：`get_page` 同时读取正文与内容对象 |
| `R` | 查询元数据 | `—` | `—` | `—` | `—`：`find_meta` 查 meta name，不是结构化对象 Query |
| `R` | 搜索正文 | `—`：可作为起始范围 | `—`：可作为起始范围 | `—`：可作为起始范围 | `M*`：OneNote index 或逐 Page 本地扫描；缺候选规模硬限制 |
| `R` | 获取父级 | `L`：根对象不适用 | `M*`：只返回 `parent_id` | `M*`：只返回 `parent_id` | `M*`：只返回 `parent_id`，不表达缩进父 Page |
| `R` | 获取路径 | `M*`：本地名称路径 | `M*`：本地名称路径 | `M*`：本地名称路径 | `M*`：本地名称路径 |
| `R` | 获取树 | `M*`：`list_hierarchy` | `M*`：`list_hierarchy` | `M*`：`list_hierarchy` | `M*`：层级深度未独立建模 |
| `R` | 获取内容 | `—` | `—` | `—` | `M`：文本、XML、内容对象、二进制 |
| `U` | 重命名 | `L`：raw hierarchy XML | `L`：raw hierarchy XML | `L`：raw hierarchy XML | `M`：`update_page_title` |
| `U` | 更新正文 | `—` | `—` | `—` | `M*`：追加、整体重建式替换、图片；缺统一 change contract |
| `U` | 更新父级 | `—` | `L`：raw hierarchy XML，未验证 | `L`：raw hierarchy XML；无 typed move | `L`：raw hierarchy XML，未验证保留 ID 的跨 Section 移动 |
| `U` | 重新排序/缩进 | `—` | `L`：raw hierarchy XML | `L`：raw hierarchy XML | `L`：raw hierarchy XML；无 typed reorder/indent/outdent |
| `D` | 删除 | `X/M*`：工具接受 Notebook，但 COM `DeleteHierarchy` 不提供 Notebook 删除语义 | `M*`：generic delete，可永久删除，无服务开关与对象确认 | `M*`：同左 | `M*`：同左；另有内容对象删除 |
| `O` | 复制 | `—` | `—` | `—` | `—` |
| `O` | 移动 | `—` | `—` | `—`：`merge_sections` 不是 Move | `—` |
| `O` | 导出 | `M`：`publish_object` | `—` | `M`：`publish_object` | `M`：`publish_object` |
| `O` | 导航 | `M` | `M` | `M` | `M`：还可定位内容对象 |
| `O` | 同步 | `M*`：generic `sync_hierarchy` | `M*` | `M*` | `M*` |
| `O` | 关闭 Notebook | `M` | `—` | `—` | `—` |
| `O` | 合并 Section | `—` | `—` | `M`：`merge_sections` | `—` |

### 4.1 README 与源码之间需要校正的表述

1. “Full CRUD for notebooks”不成立：`create_notebook`、List 和 generic resolve 存在，但 Notebook 删除不是 `DeleteHierarchy` 支持的对象语义，也没有 typed rename/update。
2. SectionGroup 并非完整 CRUD：缺独立 List/Get/Query/Update 工具，只有创建、generic hierarchy 和 generic delete。
3. `delete_hierarchy` 的 docstring 声称可删除 Notebook、SectionGroup、Section 或 Page，输入也没有排除 Notebook；这会把底层不支持包装成产品承诺。
4. `replace_page_body` 先用 `force=True` 删除多个内容对象，再写入新内容；中途失败时没有事务或回滚，不能描述成原子 Replace。
5. `max_results` 只限制 Search 命中数。若匹配很少，本地扫描仍可能读取范围内所有 Page 正文，因此它不是扫描预算。
6. `update_page_xml` 与 `update_hierarchy_xml` 让 Agent 绕过 typed validation；这与“安全 MCP”定位冲突。

## 5. 建议的完备静态对象模型

目标不是复制 Graph 字段，而是把 COM XML 中稳定、有业务意义的字段规范化，并保留来源。所有对象使用 typed model；未知 XML attribute 不直接扩展到公开返回。

### 5.1 公共字段

| 字段 | 类型 | 来源 | 约束 |
| --- | --- | --- | --- |
| `resource_type` | enum | COM XML tag | `notebook/section_group/section/page` |
| `id` | string | COM `ID` | 唯一 mutation 主键 |
| `name` | string | `name/nickname` | Page 对外命名为 `title` |
| `path` | string | 本地派生 | 只用于显示/解析，不作为写入主键 |
| `parent_id` | string/null | hierarchy 或 `GetHierarchyParent` | Notebook 为 null |
| `depth` | int | 本地派生 | 容器树深度；不冒充 Page 缩进层级 |
| `created` | datetime/null | XML 白名单属性 | 缺失时返回 null，不猜测 |
| `modified` | datetime/null | XML 白名单属性 | mutation 可作为乐观并发确认 |
| `is_in_recycle_bin` | bool | XML 白名单属性 | 默认列表排除，但对象状态可表达 |
| `relationship_source` | enum | 本地 | `com/derived` |

### 5.2 Notebook

| 字段 | 需求 | 说明 |
| --- | --- | --- |
| `id`、`name`、`path` | 必读 | `path` 对本地 Notebook 具有导航价值，但仍不是调用授权。 |
| `section_group_ids`、`section_ids` | 按需展开 | 只返回直属子级；完整树走独立 Get Tree。 |
| `is_open` | 建议读取 | 与 `close_notebook` 对称；必须以 COM 可验证字段为准。 |
| `sync_state` | 可选 | 没有可靠状态来源时不应因存在 `sync_hierarchy` 动作而虚构。 |

### 5.3 SectionGroup

| 字段 | 需求 | 说明 |
| --- | --- | --- |
| `id`、`name`、`notebook_id` | 必读 | 不再只保留 `notebook_name`。 |
| `parent_section_group_id` | 必读，可空 | 区分 Notebook 直属组与嵌套组。 |
| `depth` | 必读 | 用于递归限制与路径展示。 |
| `section_ids`、`section_group_ids` | 按需展开 | 均为直接子级。 |

### 5.4 Section

| 字段 | 需求 | 说明 |
| --- | --- | --- |
| `id`、`name`、`notebook_id` | 必读 | 四层稳定关系的基础。 |
| `parent_section_group_id` | 必读，可空 | 创建和同 Notebook 移动时决定目标父级。 |
| `page_count` | 选读、派生 | 必须基于完整列表，不用不完整扫描冒充准确值。 |
| `is_locked`、`is_read_only` | 条件读取 | 仅在 COM XML 有稳定证据时公开；mutation 前应检查。 |

### 5.5 Page

| 字段 | 需求 | 说明 |
| --- | --- | --- |
| `id`、`title`、`notebook_id`、`section_id` | 必读 | `title` 不再复用容器 `name`。 |
| `page_level`、`order` | 树读取必需 | 来自 COM hierarchy；与全局 `depth` 分开。 |
| `parent_page_id` | 派生，可空 | 从同 Section 的完整有序 Page 序列推导并标记 `derived`。 |
| `has_children` | 派生 | 完整 Page 列表后计算。 |
| `text_preview` | 显式选读 | 默认元数据列表不读取完整 Page XML。 |
| `content`、`objects` | 独立读取 | 大对象和二进制永不进入普通 List/Get Metadata。 |

### 5.6 PageContentObject

当前 `collect_page_objects` 可演进为独立模型：`id`、`page_id`、`kind`、`parent_object_id`、`container_object_id`、`callback_id`、`media_type/format`、`can_delete`、`delete_target_id`。读取二进制必须使用服务端校验过的 Page/Object 上下文，不能把任意 callback ID 当作全局句柄。

## 6. 建议的目标对象—操作矩阵

这张矩阵应成为 README、工具注册和实施路线的权威入口。它只表达希望交付的 typed operations；raw XML 不算对象能力。

状态：`P0` 为快速自用产品首期，`P1` 为对象模型补全，`P2` 为高风险组合能力，`V` 为需要隔离 Notebook 实测，`X` 为不承诺。

| 类别 | 操作 | Notebook | SectionGroup | Section | Page |
| --- | --- | --- | --- | --- | --- |
| `C` | 创建 | `P0` | `P0` | `P0` | `P0` |
| `R` | 列出 | `P0` | `P0` | `P0` | `P0` |
| `R` | 获取元数据 | `P0` | `P0` | `P0` | `P0` |
| `R` | 查询元数据 | `P1` | `P1` | `P1` | `P1` |
| `R` | 搜索正文 | `—` | 仅作范围 | 仅作范围 | `P0`：Notebook/SectionGroup/Section 范围可配置 |
| `R` | 获取路径 | 根对象 | `P0` | `P0` | `P0` |
| `R` | 获取树 | `P0` | `P0` | `P0` | `P0` |
| `R` | 获取内容 | `—` | `—` | `—` | `P0`：text/XML/object/binary 分工具 |
| `U` | 重命名 | `V`：路径与 nickname 语义需分开 | `P1` | `P1` | `P0` |
| `U` | 更新正文 | `—` | `—` | `—` | `P0`：typed append/replace/add image |
| `U` | 更新父级 | 根对象 | `V` | `P1`：先验证同 Notebook 保持 ID | `V`：跨 Section 保持 ID 未证实 |
| `U` | 重新排序 | `—` | `V` | `V` | `P1` |
| `U` | 缩进/取消缩进 | `—` | `—` | `—` | `V`：必须验证既有 Page 身份与整棵子树 |
| `D` | 删除 | `X`：Close 不等于 Delete | `P0`：默认进回收站 | `P0`：默认进回收站 | `P0`：默认进回收站 |
| `O` | 复制 | `P2`：导出/导入式组合 | `P2` | `P2` | `P2` |
| `O` | 移动 | `X/V` | `V` | `P1`：同 Notebook typed move | `P2`：若只能复制后删除，必须叫重建式 Move |
| `O` | 导出 | `P1` | `X/V` | `P1` | `P1` |
| `O` | 导航 | `P0` | `P0` | `P0` | `P0` |
| `O` | 同步 | `P1` | `V` | `V` | `V` |
| `O` | 关闭 Notebook | `P1` | `—` | `—` | `—` |
| `O` | 合并 Section | `—` | `—` | `P2`：破坏性、不可逆语义单列 | `—` |

## 7. 动态操作契约建议

### 7.1 命名和参数

- 每个工具只做一个动词：`list_section_groups`、`get_section_group`、`rename_section`、`move_section`；
- 参数使用 `notebook_id`、`section_group_id`、`section_id`、`page_id`，不在 mutation 中使用泛化 `object_identifier`；
- 人类友好的路径/名称解析只存在于 `resolve_*` 或只读 Query，解析后返回 ID；
- 返回统一使用 `{ok, item/result, warnings, complete}`，错误使用固定 `code`，不直接返回 COM message、XML 或本机路径。

### 7.2 写入与删除保护

快速自用不等于默认裸写。最低限度应有：

- `LOCAL_ONENOTE_ENABLE_WRITES=false` 默认关闭创建和更新；
- `LOCAL_ONENOTE_ENABLE_DELETES=false` 独立控制删除；
- 永久删除再加 `LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES=false`，默认只进 OneNote 回收站；
- 删除参数必须包含对象 ID、`expected_name`、`expected_parent_id`，能读取修改时间时再要求 `expected_modified`；
- mutation 前后均按 ID 回读，验证类型、父级、名称和预期状态；
- `force` 不向普通 Agent 工具暴露。确有需要时作为本机管理员配置，而不是调用参数。

### 7.3 Query 与 Search

对象 Query 和正文 Search 必须分开：

- Query 只处理层级元数据，公开结构化条件，如 `name_equals`、`modified_after`、`parent_id`、`is_in_recycle_bin`；
- Search 只返回 Page，并显式指定 Notebook、SectionGroup 或 Section 范围；全局范围必须是单独配置，而不是空字符串的隐式含义；
- OneNote index 结果应标记 `backend=onenote_index`；fallback 到本地扫描不能静默改变成本模型；
- 本地扫描先枚举候选 Page 元数据，受 `MAX_SEARCH_PAGES` 硬限制；还需限制单页字符/字节、总下载字节、总耗时、并发数和片段长度；
- `max_results` 只能限制返回命中数，不能替代扫描预算。

### 7.4 Page 内容修改

保留 `update_page_title`、`append_to_page`、`add_image_to_page` 的 typed 方向，但调整：

- 将 `replace_page_body` 明确命名为重建式替换，并在执行前生成操作计划；
- 删除多个内容对象后任一步失败都返回 `partial=true` 和已完成步骤，不宣称原子成功；
- 内容对象删除只接收从 `get_page_objects` 返回且再次回读确认的 target；
- raw `update_page_xml`、`update_hierarchy_xml` 默认不注册。开发模式需要它们时，放入独立 server profile，并强制本机显式启用。

## 8. 建议的代码分层

```text
tools/                     MCP 单一动词、typed 输入输出
domain/
  notebook.py
  section_group.py
  section.py
  page.py
  page_content.py          稳定领域模型和操作结果
services/
  hierarchy_service.py     路径、树、Query、Search 预算
  mutation_service.py      开关、确认、回读、部分失败
adapters/com/
  client.py                长驻或有界 COM 执行器
  hierarchy_mapper.py      XML → typed model
  page_mapper.py           Page XML → content model
  xml_builder.py           只由 typed service 调用
```

现有 `bridge.py` 可以继续做底层白名单，但应考虑长驻单线程 COM broker，显式初始化 COM apartment，并把超时、进程重启、OneNote 忙碌和 HRESULT 映射为固定安全错误码。是否值得长驻必须用启动、连续读取、搜索和批量写基准测试决定。

## 9. 实施顺序

### P0：快速自用但边界可信

1. 建立五个 typed model 和稳定序列化字段；未知 XML attribute 不再直接公开。
2. 补 `list/get` 四层对称工具，保留 `resolve_identifier` 作为只读辅助。
3. 增加写入、删除、永久删除三层服务开关和 mutation 回读确认。
4. 修正 `delete_hierarchy`：明确拒绝 Notebook；拆成 `delete_section_group`、`delete_section`、`delete_page`。
5. 为 Search 增加显式范围与候选 Page 硬预算；不静默从 index fallback 到全量扫描。
6. 默认移除两个 raw XML 工具和 `force` 参数。

### P1：让对象模型真正优于现有 COM MCP

1. 实现结构化 Query、Get Path、Get Tree 与 Page 缩进树重建。
2. 提供 typed `rename_section_group`、`rename_section`、`reorder_page`。
3. 在唯一命名隔离 Notebook 中验证同 Notebook `move_section` 是否保持 ID、内容与顺序，再决定交付状态。
4. 统一导出、导航、同步的对象适用范围和返回契约。

### P2：谨慎组合能力

1. Copy/Move 先生成计划并估算对象数、字节数和破坏范围。
2. 任何“复制后删除”必须明确叫重建式 Move，返回新旧 ID 和部分失败状态。
3. `merge_sections`、永久删除、整页重建等能力使用更严格确认和独立配置。

## 10. 验收标准

- README 的能力表由目标对象—操作矩阵生成或逐项对照，不再使用模糊的“Full CRUD”；
- 四层对象均有独立静态字段契约，Page 元数据与正文/二进制分离；
- 每个矩阵中的 `P0/P1` 项都有对应 typed tool、Mock、开关与回读证据；
- generic/raw 工具不能绕过 mutation policy；
- Search 测试证明候选 Page 超限时在读取正文前拒绝；
- 删除测试覆盖开关关闭、确认不一致、对象类型错误、默认回收站、永久删除禁用和删除后回读；
- COM 能力不确定项在隔离 Notebook 实测前保持 `V`，不因 XML 字段或 low-level method 存在就标成已支持。

## 11. 最终取舍

COM 方向适合快速自用，而且正文搜索、离线访问、Page 内容对象、回收站删除、导出和桌面导航确实比纯 Graph 路线更贴近个人生产力。但最值得迁移的不是当前参考项目的工具表，而是它已经打通的 COM bridge 和 XML 处理经验。

产品层应继续采用更完备的四层对象模型：静态字段独立、动态操作单义、实际能力和候选能力分开、对象—操作矩阵居中。这样既保留 COM 的本地优势，也不会把 raw XML 的自由度误认为产品模型的完整度。
