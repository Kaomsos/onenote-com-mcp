# Local OneNote MCP 对象模型概念评估

> 文档性质：产品概念模型
> 对齐方案：当前 53 Tool User profile
> 更新日期：2026-08-15

## 文档边界

本文只解释 Local OneNote MCP 如何理解 OneNote 的对象、关系、标识、读取层次和操作语义，帮助产品设计与工具分类保持一致。本文不记录 commit、测试数量、开发阶段、实现模块、bridge 调用方式或人工验证进度。

当前已实现的工具合同以 [工具参数与返回格式](../design/tool_contracts.md) 为准；[TODO 034](../todo/034_pre_user_testing_tool_surface_convergence.md) 记录发布面收敛和验收证据。本文的对象—操作矩阵只描述当前产品概念，不记录开发过程或具体实现。

## 1. 核心对象

OneNote 内容域由四层 hierarchy object 和一类 Page 内对象组成：

```text
Notebook
├─ SectionGroup
│  ├─ SectionGroup（可嵌套）
│  └─ Section
└─ Section

Section
└─ Page
   └─ PageContentObject
```

### Notebook

Notebook 是 hierarchy 根对象，也是 SectionGroup 与 Section 的顶层容器。它有独立 identity、显示名称和打开/关闭生命周期，但产品模型不承诺删除 Notebook。

### SectionGroup

SectionGroup 是可嵌套容器，父级可以是 Notebook 或另一个 SectionGroup。它可以包含 SectionGroup 与 Section，不直接包含 Page。

### Section

Section 是 Page 的直接容器，父级可以是 Notebook 或 SectionGroup。Section 的 sibling order 可调整；SectionGroup 的稳定自定义 sibling order 不属于产品能力。

### Page

Page 始终直接属于一个 Section。它有 title、同 Section 内的 order 和 indentation level。OneNote UI 中的“子页”不是容器嵌套，而是同一 Section 内 Page 顺序与缩进形成的视觉关系。

### PageContentObject

PageContentObject 是 Page 内可寻址的内容对象，例如 outline、image 或其他受支持表示。它不能脱离所属 Page 独立存在；读取 binary 或删除对象时，必须在 Page 的当前对象集合中重新确认对象 identity。

## 2. 两类关系

### 2.1 容器父级

| 对象 | 允许的直接容器父级 |
| --- | --- |
| Notebook | 无；它是根对象。 |
| SectionGroup | Notebook 或 SectionGroup。 |
| Section | Notebook 或 SectionGroup。 |
| Page | Section。 |
| PageContentObject | Page。 |

容器父级决定对象归属、可用目标类型和 hierarchy path。

### 2.2 Page 缩进关系

`parent_page_id` 是根据同 Section 的完整有序 Page 序列与 indentation level 派生的便利关系。它不改变 Page 的容器父级；即使 UI 显示为子页，Page 仍直接属于 Section。

因此：

- `include_subpages=false` 表示只选择目标 Page；
- `include_subpages=true` 表示选择该 Page 以及连续缩进在其下的 Page；
- Page Reorder 可以改变 order 和 indentation，从而改变派生的 `parent_page_id`；
- Page Reparent 改变所属 Section，和只改变缩进的 Reorder 不是同一操作。

## 3. 标识、名称与路径

### Identity

COM object ID 是 typed 操作的主标识。读取发现可以从 Notebook 列表、Query 或 Search 得到候选；一旦选择对象，后续精确读取与 mutation 应使用 ID。

### Name 与 title

Notebook、SectionGroup、Section 使用 name；Page 使用 title。名称和标题适合显示、筛选和确认，不适合作为 mutation 的唯一定位条件。

### Path

Hierarchy path 是供人理解的派生显示信息。Rename、Reparent 或容器变化都可能改变 path，因此 path 不能取代稳定 ID，也不能成为写入对象的隐式选择器。

### Mutation confirmation

Mutation 除精确 ID 外，还应携带对象类型适用的 expected name/title、expected parent/container 和可选 modified 信息。它们用于发现调用前后状态漂移，不把名称提升为主键。

### Identity continuity

不同操作对 identity 的承诺不同：

| 操作 | Identity 语义 |
| --- | --- |
| Rename | 同一对象，ID 应保持。 |
| Reorder | 同一父级内调整，ID 应保持。 |
| Organize / Reparent | 改变容器父级；后端可能重映射部分对象或内容 ID，必须回传最终对象。 |
| Copy | 创建新对象和新 ID。 |
| Reconstructive Move | Copy、验证目标、再可恢复删除源；目标使用新 ID。 |
| Delete | 原对象进入可恢复删除状态，不产生替代 ID。 |

## 4. 发现、读取与内容分层

用户任务按意图选择入口，不把所有读取混成通用 Get：

| 层次 | 回答的问题 | 当前工具族 |
| --- | --- | --- |
| Session | OneNote 是否可用，是否需要显式启动 GUI | `health_check`、`launch_onenote_gui` |
| Hierarchy Browse | 有哪些 Notebook，对象下面有什么 | `list_notebooks`、`expand_*`、`get_hierarchy_path` |
| Metadata Get | 已知 ID 的单个对象是什么 | `get_*_metadata` |
| Query & Search | 哪些对象符合属性或正文条件 | `query_*`、`search_pages` |
| Page Content Read | Page 显示了什么、有哪些对象、对象 binary 是什么 | text、object list、object binary 三个 typed 入口 |

重要区分：

- Browse 用于沿 hierarchy 有界展开；
- Metadata Get 是 exact-ID 单对象读取；
- Query 按 hierarchy metadata 发现候选；
- Search 按 Page 正文发现候选；
- Page metadata 不隐式读取正文、对象清单或 binary；
- raw Page XML 是底层表示，不是普通用户读取的降级入口。

## 5. 操作语义

### Create

在类型允许的容器中创建新对象。Create 不接受按名称猜测目标；目标容器必须使用精确 ID。

### Rename

只改变对象显示名称或 Page title，不改变父级、顺序或内容。Notebook Rename 不在 v1.0 产品能力中。

### Reorder

对象保持在同一容器父级内，改变 sibling order；Page 还可改变 indentation。SectionGroup 没有稳定的自定义顺序能力，因此不提供 Reorder。

### Organize / Reparent

改变对象的直接容器父级，公开工具仍命名为 `reparent_*`。它只适用于 Page、Section 和 SectionGroup，并限于产品合同允许的目标关系。Reparent 不等于同父级 Reorder，也不等于 Copy 后 Delete。

### Page Content Mutation

Append 在 Page 中追加内容；Add Image 从本地文件增加图像；Replace Body 替换正文但不更改 title；Delete Content Object 删除 Page 内已确认的对象。Replace 是多阶段 mutation，概念上同时需要写入和删除授权。

### Recoverable Delete

默认 `delete_*` 只表达可恢复删除。永久删除是不同风险的操作，不能由布尔参数切换；如未来发布，应使用独立名称、exposure 和授权。

### Copy

在允许的目标容器创建逻辑副本与新 ID。递归对象的范围必须由 typed 参数表达，不能由客户端手工拼装 raw XML。

### Reconstructive Move

Move 是 Copy、目标验证、源状态复核和源可恢复删除组成的非原子操作。它不是原生 identity-preserving move，不设独立 Move 授权；风险来自写入、复制和删除三个组成部分。

### File、GUI 与 Lifecycle

导出本地 PDF、控制 OneNote GUI、请求 Notebook sync 和关闭 Notebook 都不是普通只读。它们分别属于 Local File IO、UI Control 和 Notebook Lifecycle effect，必须在授权与描述中显式可见。

## 6. 对象—操作矩阵

符号：`●` 表示 User profile 提供 typed 能力；`◇` 表示通过所属对象间接适用；`—` 表示不适用或明确不提供。矩阵表达产品概念，不代替每个工具的详细 schema。

| 操作 | Notebook | SectionGroup | Section | Page | PageContentObject | 目标工具或说明 |
| --- | :---: | :---: | :---: | :---: | :---: | --- |
| List roots | ● | — | — | — | — | `list_notebooks` |
| Expand hierarchy | ● | ● | ● | ● | — | 四个 typed Expand 与 `expand_hierarchy` |
| Get hierarchy path | ● | ● | ● | ● | — | `get_hierarchy_path` |
| Get metadata | ● | ● | ● | ● | — | 四个 `get_*_metadata` |
| Query metadata | ● | ● | ● | ● | — | 四个 `query_*` |
| Search Page text | — | ◇ | ◇ | ● | — | `search_pages`；scope 可由 hierarchy 对象限定 |
| Read Page text | — | — | — | ● | — | `get_page_text` |
| Get content objects | — | — | — | ● | ● | `get_page_content_objects` 返回 Page 下对象 |
| Get content object binary | — | — | — | ◇ | ● | `get_page_content_object_binary` 以 Page 和对象 identity 复核 |
| Create | ● | ● | ● | ● | — | 四个 `create_*` |
| Rename | — | ● | ● | ● | — | `rename_section_group`、`rename_section`、`rename_page` |
| Reorder | — | — | ● | ● | — | Section sibling order；Page order/indentation |
| Organize / Reparent | — | ● | ● | ● | — | 三个 `reparent_*` |
| Append content | — | — | — | ● | — | `append_page_content` |
| Add image from file | — | — | — | ● | ● | `add_page_image_from_file` 创建内容对象 |
| Replace body | — | — | — | ● | ◇ | `replace_page_body` 作用于 Page 正文对象集合 |
| Delete content object | — | — | — | ◇ | ● | `delete_page_content_object` |
| Recoverable delete | — | ● | ● | ● | — | 三个默认 `delete_*`；Notebook Delete 不支持 |
| Copy | ● | ● | ● | ● | — | 四个 `copy_*` |
| Reconstructive Move | — | ● | ● | ● | — | 三个 `move_*`；Notebook Move 不支持 |
| Get hyperlink | ● | ● | ● | ● | ● | `get_hyperlink`，对象链接类型为 desktop/web |
| Export PDF | ● | — | ● | ● | — | `export_object_to_pdf` |
| Navigate GUI | ● | ● | ● | ● | ● | `navigate_to` |
| Request sync | ● | — | — | — | — | `request_notebook_sync` |
| Close | ● | — | — | — | — | `close_notebook`；Close 不是 Delete |

Session 的 `health_check` 和 `launch_onenote_gui` 面向 OneNote Desktop session，不属于某个内容对象，因此不放入对象列。

## 7. 权限概念模型

当前产品使用七个默认关闭的授权类别：Writes、Deletes、Organize、Copy、Local File IO、UI Control、Notebook Lifecycle。

| 操作类别 | 需要的授权 |
| --- | --- |
| Create、Rename、Reorder、Append | Writes |
| Replace Body | Writes + Deletes |
| Content Object Delete、Recoverable Delete | Deletes |
| Organize / Reparent | Writes + Organize |
| Copy | Writes + Copy |
| Reconstructive Move | Writes + Copy + Deletes |
| Add Image from File | Writes + Local File IO |
| Export PDF | Local File IO |
| Launch / Navigate | UI Control |
| Sync Request / Close | Notebook Lifecycle |

授权描述的是 effect，不描述工具是否公开。普通 typed read 不需要 mutation 授权，但仍受 scope、分页、字符、binary 大小和耗时预算约束。

## 8. 公开、孵化与内部边界

对象模型不应把每个底层能力都投影成 MCP Tool：

- User profile 只保留稳定、typed、能映射到明确用户任务的入口；
- `resolve_identifier`、`get_page_xml`、`navigate_to_url` 作为 incubating 能力集中管理，不进入 User profile；
- `get_special_locations`、`get_parent` 是 internal helper，不是用户任务；
- raw hierarchy/page mutation、generic destructive operation、公开 plan/preview/token 不属于产品对象模型；
- Internal & Incubating catalog 只是非注册目录，不是可批量开启的隐藏 profile。

这一边界保证“底层实现可复用”不会自动变成“用户应该看见一个工具”。

## 9. 长期不变量

- local-only、COM-first，不引入 Graph、OAuth、遥测或远程内容处理；
- typed object 与 exact ID 优先，名称和路径不成为 mutation 主键；
- Page 缩进关系与 hierarchy 容器关系保持分离；
- Notebook Delete、SectionGroup Reorder 和任意 raw XML mutation 不作为稳定产品能力；
- Close 不等于 Delete，Sync Request 不等于 Sync Complete；
- Replace、递归 Copy 和 Reconstructive Move 都可能是多阶段、非原子操作；
- Binary、Search 与 Expand 必须有硬预算，不能形成无界扫描；
- mutation 权限 fail closed，真实 OneNote mutation 只能由用户在具名隔离场景中显式启动。

## 相关文档

- [TODO 034：用户测试前 MCP 工具发布面收敛](../todo/034_pre_user_testing_tool_surface_convergence.md)
- [TODO 031：显式 launch_onenote_gui 工具](../todo/031_start_onenote_desktop_tool.md)
- [当前 OneNote 对象模型合同](../design/object_model.md)
- [当前公开 Tool 契约](../design/tool_contracts.md)
- [内部低层与诊断操作](../design/advanced_operations.md)
