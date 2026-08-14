# Local OneNote MCP 对象模型阶段性复审

> 历史审计基线：commit `42092e5`，2026-08-04
> 本次复审对象：commit `d11c5a0`，2026-08-10
> 变更范围：基线之后 33 个 commit
> 方法：静态检查生产代码、当前设计契约、自动化测试与已保存的人工验证记录；未操作真实 OneNote 数据。
> 结论补充：2026-08-10 用户触发的 Reorder 隔离验证；Section 保留，SectionGroup 明确拒绝。
> 合同同步：2026-08-10 后续工作树已实现全部已打开 Notebook 搜索与默认单页 Page Copy；Page Copy 已获用户真实证据，全局搜索仍待独立验收。
> 取证范围同步：2026-08-11 后续 Copy 内容取证聚焦 InkDrawing、UI Shape、MediaFile；FileAttachment、MeetingInfo 与 Embedded Spreadsheet（内嵌电子表格）排除。
> Move 同步：2026-08-11 用户确认更新后的 Page root-only/subtree 以及跨 Notebook Section/SectionGroup 三个 Move 场景全部通过；证据分别来自 `run-2026-08-11-20-29-19`、`run-2026-08-11-20-31-28`、`run-2026-08-11-20-33-29`。
> Query 实施同步：2026-08-13 阶段 A 已注册 `query_notebook`、`query_section_group`、`query_section`、`query_page`，移除 `query_hierarchy`，并交付 root/单一起点、open-only、最浅 scope、live pagination 与 human-gated 场景。五个 `list_*` 在真实场景完成和用户单独批准前继续保留。

## 核心阅读入口

本文是带明确时间范围的阶段性 overview，不是当前公开契约的唯一来源。实现或接口变化后，应优先更新以下 canonical 文档，再更新本报告：

- [当前设计架构](../design/architecture.md)：分层、依赖方向和运行时生命周期；
- [OneNote 对象模型](../design/object_model.md)：静态字段、关系和 mutation 一致性；
- [工具参数与返回格式](../design/tool_contracts.md)：公开工具、policy、预算和响应 envelope；
- [Advanced/低层操作](../design/advanced_operations.md)：开发 profile、raw XML 与受控能力探针边界；
- [隔离 mutation 验证](../dev/isolated_mutation_validation.md)：真实 OneNote 验证的权限与证据边界。

本次复审将两个维度分开：

1. **实现状态**回答能力是否存在于默认 typed、实验性或 advanced profile；
2. **证据状态**回答能力只有自动化合同，还是已有用户确认的真实 OneNote 证据。

不得用“已经实现”推导“所有 OneNote/Office 版本均已验证”，也不得用某个底层 COM/raw XML 操作推导稳定对象能力已经交付。

## 1. 结论先行

2026-08-04 审计提出的核心方向——“对象模型优先，COM adapter 居后”——已经成为当前架构，而不再只是重构建议。默认 MCP profile 现有 61 个工具，另有 6 个只在显式启用时注册的 advanced 工具。Notebook、SectionGroup、Section、Page 和 PageContentObject 已有独立 typed model；业务规则从 `server.py` 移入 services；默认 mutation 使用精确 ID、confirmation fields、独立 policy 和操作后回读。

原审计列出的主要产品边界也大多已经落实：

- 四层 Create/List/Get、Path、Tree 和 Page 缩进树已形成 typed 契约；四个 fixed-type metadata Query 已采用原生 root/单一起点、open-only 和 live pagination，List 仍处于待真实验证与单独退役批准的迁移窗口；
- Page 元数据、XML、文本、内容对象和二进制已拆分读取；
- SectionGroup/Section Rename、Page Reorder 和三类 typed Delete 已实现；Section 同父级 Reorder 已有用户确认的真实 UI 证据；SectionGroup Reorder 因后端只支持按名称固定升序而明确拒绝；
- Search 要求显式 root/start-node scope，固定调用 OneNote index，并具有分页前候选 Page、当前页单页字符、总字符和总耗时硬预算；
- Writes、Deletes、Permanent Deletes、统一 Reparent、Copy、Page Move、容器 Move 和 raw XML 分别 fail closed；
- raw XML 与 legacy generic destructive 工具不进入默认 profile；
- 四层 Copy、Page Move 与跨 Notebook Section/SectionGroup Move 已有实验实现、Move 专属 plan digest、预算和部分失败语义；容器 Move 只允许跨 Notebook，并只在共享 `copy_contract_satisfied`、完整单射映射和源重校验通过后执行一次非永久根删除。Move 不另设 lossless 或逐类别门禁。

项目当前的主要差距已从“缺少稳定对象模型”转移到“扩展能力与跨环境证据”：用户已确认四层 Copy、默认单页/显式子树 Page Copy、更新后的 Page Move、跨 Notebook Section/SectionGroup Move 和三类迁移后的 typed Reparent 场景在当前环境完成真实闭环。InkDrawing、UIShape、本地录像 `MediaFile` 和 `InsertedFile` 已完成 lossless Copy。FileAttachment 因当前 GUI 无法生成独立表示而排除，MeetingInfo 因小众、难生成且价值低而排除；Embedded Spreadsheet（内嵌电子表格）尚无公开 `kind`/XML 证据，按当前产品范围明确不支持。

## 2. 复审范围与证据等级

### 2.1 证据优先级

| 等级               | 能证明什么                                            | 本次使用的来源                                        |
| ------------------ | ----------------------------------------------------- | ----------------------------------------------------- |
| 当前实现事实       | 当前代码注册、校验和返回的行为                        | `src/local_onenote_mcp/`、当前 design 文档          |
| 自动化合同         | policy、编排、错误与边界条件按项目合同工作            | `tests/` 与 `tests/manual_validation/tests/`      |
| 用户确认的真实证据 | 指定 OneNote/Office 环境中的真实 COM 副作用或 UI 结果 | TODO 中记录的 run、manual-validation evidence、Lesson |
| 尚未确认           | 只有实现、Mock、dry-run 或工程推断                    | 进行中/待办 TODO 和未进入保真 allowlist 的类型        |

截至 2026-08-13，当前工作树的完整纯自动化结果为 `845 passed`。该结果证明离线合同通过，不证明真实 OneNote mutation 已普遍通过。复审过程没有由 Agent 运行真实 `tests/manual_validation/run.py <scenario>` 或 `run.py all`；仅执行了无副作用 dry-run。Section/SectionGroup Reorder、三类 Reparent、四层 Copy、更新后的 Page Move 与 Section/SectionGroup Move 的真实结果都来自用户本人显式启动的隔离场景；用户已确认其中受支持能力在当前环境完成验收，SectionGroup Reorder 则以固定名称升序的负能力证据结束评估。

### 2.2 历史基线问题的完成度

| 2026-08-04 基线问题                                            | 当前状态                                                                                                                                                                              | 结论                                          |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| 四层对象压成扁平`HierarchyItem`，未知 XML attribute 直接扩散 | 五个 domain model 与白名单 hierarchy mapper 已实现                                                                                                                                    | 已解决                                        |
| `server.py` 同时承担工具、业务规则和 XML 编排                | 已拆为 composition root、tools、services、domain/page/hierarchy 和 bridge                                                                                                             | 已解决                                        |
| mutation 可用 ID、路径或唯一名称定位                           | 默认 typed mutation 使用精确 ID 和 confirmation fields；名称/路径仅用于只读辅助或 advanced 工具                                                                                       | 已解决                                        |
| 无独立 Writes/Deletes/Permanent Deletes/raw XML 开关           | 风险能力使用相互独立、默认关闭的 policy                                                                                                                                               | 已解决                                        |
| Notebook 被 generic Delete 工具错误承诺                        | typed Delete 不提供 Notebook；advanced generic Delete 也显式拒绝 Notebook                                                                                                             | 已解决                                        |
| Search 只限制返回命中数，不限制候选与 hydration 成本                        | 已增加严格 scope、分页前候选数、当前页单页字符、总字符和时间预算                                                                                                                                  | 已解决；仍不是字节预算                        |
| raw Page/Hierarchy XML 默认暴露                                | raw Page XML 只在 6-tool advanced profile 显式启用；raw hierarchy MCP 工具已从所有生产 profile 移除，内部 bridge operation 仅供受约束 service 使用                                      | 已解决                                        |
| `replace_page_body` 容易被理解为原子 Replace                 | 当前合同明确为非原子，失败返回`partial_failure/completed_steps`                                                                                                                     | 已解决；尚无独立执行计划                      |
| SectionGroup 缺 typed List/Get，四层缺 Query/Get Tree          | 对称 List/Get、Path、Tree 与四层 typed Query 已实现；Query 使用原生 scope、open-only 与固定资源类型合同，List 退役仍等待真实证据和用户批准                                      | Query 已解决；List 迁移门待完成                  |
| Rename、Reorder、Reparent、Move 和 Copy 缺稳定能力边界         | Rename 已 typed；Page/Section Reorder 有明确契约，SectionGroup Reorder 因后端固定名称升序而拒绝；三类同 Notebook Reparent 已 typed、独立门控并由用户确认当前环境真实通过；四层 Copy、Page Move 和跨 Notebook 容器 Move 已实验实现且取得当前环境真实证据 | 能力与证据边界已明确 |

## 3. 当前架构与对象模型

### 3.1 当前调用链

```text
MCP client
  → server.py：composition root 与工具注册
  → tools/：参数和统一 response envelope 适配
  → services/：policy、确认、预算、编排和回读
  → hierarchy.py / page/：typed mapper 与 Page 内容处理
  → domain/：稳定对象模型
  → bridge.py：固定 PowerShell/COM operation 与 JSON transport
  → OneNote.Application COM
```

`server.py` 不再实现业务逻辑。`tools/` 不直接调用 COM，`services/` 是 mutation policy 和可恢复失败的主要边界，`domain/` 不依赖 MCP transport 或 bridge。详细依赖方向以 [当前设计架构](../design/architecture.md) 为准。

保留下来的基础设施限制是：每次 bridge 调用仍会启动非交互 PowerShell，并在该进程创建 OneNote COM 对象。manual runner 已将一个 scenario 的 MCP 启动数收敛到最多一个，但这不等于 bridge 已变成长驻 COM broker，也不消除 scenario 内的多次 PowerShell/COM 调用。

### 3.2 已落地的 typed 对象

当前公开模型的容器结构为：

```text
Notebook
├─ SectionGroup
│  ├─ SectionGroup（可继续嵌套）
│  └─ Section
└─ Section

Section → Page → PageContentObject
```

- `Resource` 提供白名单公共字段；Notebook、SectionGroup、Section 和 Page 提供各自稳定关系字段；
- Page 使用 `title`，并把容器 `parent_id` 与缩进关系 `parent_page_id` 分开；
- Page 的 `order/page_level/parent_page_id/has_children` 基于同 Section 完整有序序列派生；
- 普通 List/Get 不读取 Page 正文或二进制；
- PageContentObject 绑定 `page_id`，二进制 callback 和删除目标都会在当前 Page 的最新对象快照中复核。

完整字段与 null/derived 规则不在本报告复制，见 [OneNote 对象模型](../design/object_model.md)。

### 3.3 容器父级与 Page 缩进关系

本文中的“父级”只表示 OneNote hierarchy 中决定对象归属的直接容器，不把 UI 中 Page 的子页/子子页缩进关系算作容器父子关系：

| 对象         | 容器父级                       |
| ------------ | ------------------------------ |
| Notebook     | 无父级，是 hierarchy 根对象    |
| SectionGroup | Notebook 或另一个 SectionGroup |
| Section      | Notebook 或 SectionGroup       |
| Page         | Section                        |

所有 Page 都是所属 Section 的直接子对象。UI 中显示的子页、子子页仍是彼此独立的 Page；它们的树形外观来自同一 Section 内独立的 `order` 与 `page_level` 属性。`parent_page_id` 是项目根据完整有序 Page 序列派生出的便利关系，不是 COM hierarchy 中的容器父级，也不改变 Page 始终归属于 Section 的事实。

因此，本文中的关系操作按以下边界解释：

- **Reorder**：对象保持在同一容器父级内并保持 ID，只改变兄弟顺序；Page Reorder 还可以改变 `page_level`，从而改变派生的 `parent_page_id`。Section Reorder 由独立实验开关保护。SectionGroup 后端集合只有按名称固定升序，没有可变 sibling order，因此无论父级是 Notebook 还是 SectionGroup 都拒绝 reorder；Rename 或 Copy/Delete 不能作为隐式排序替代品。真实证据和最终处置见 [TODO 006](../todo/006_typed_section_and_section_group_reorder.md)。
- **Copy**：按对象类型选择允许的目标容器并创建新 ID；Page 的目标是 Section，Section 的目标是 Notebook 或 SectionGroup，SectionGroup 的目标是 Notebook 或 SectionGroup（不得为自身或后代），Notebook Copy 创建独立 Notebook。
- **Reparent**：只在同一 Notebook 内改变对象的容器父级，不使用 Copy/Delete，也不等同于同父级 Reorder。Page 默认只迁移选中对象并提升被排除后代，也可显式迁移完整缩进子树；目标根归一化为 level 1。OneNote 可在 Page Reparent 时重映射 Page 和内容对象 ID，因此 Reparent 不承诺所有对象类型都保持 ID；具体返回与验证以对象—操作矩阵为准。
- **Move**：表示 Copy、完整验证目标，再对源执行非永久删除；可创建新 ID。Page Move 支持 root-only/完整缩进子树；Section/SectionGroup Move 只允许跨 Notebook、始终选择完整容器子树，并只调用一次源根非永久删除。同 Notebook 容器位置变化继续使用 Reparent。

### 3.4 标识符与 mutation 一致性

默认 typed mutation 以 COM object ID 为唯一主键：

- 容器回传 `expected_name`、`expected_parent_id` 和可选 `expected_modified`；
- Page 回传 `expected_title`、`expected_section_id` 和可选 `expected_modified`；
- service 在写前校验类型和确认字段，写后按同一 ID 或明确的新 ID 回读预期状态；
- `resolve_identifier` 继续为交互式只读辅助提供 ID、路径、唯一名称解析，但不授权默认 mutation。

这一区分使路径仍可用于显示和人工定位，同时避免路径或名称变化静默改变写入目标。

## 4. 对象—操作矩阵

实现状态：

| 标记   | 含义                                                                                   |
| ------ | -------------------------------------------------------------------------------------- |
| `T`  | 默认 profile 注册的 typed 契约；mutation 仍受默认关闭的通用 Writes/Deletes policy 控制 |
| `E`  | 默认 profile 注册的实验 typed 契约；除通用 policy 外还需要独立实验开关                 |
| `X`  | 当前明确不支持或不承诺                                                                 |
| `—` | 对该对象不适用                                                                         |

关系术语统一见 [§3.3 容器父级与 Page 缩进关系](#33-容器父级与-page-缩进关系)；历史基线问题与当前完成度见 [§2.2](#22-历史基线问题的完成度)。本矩阵只评价默认 profile 中的 typed 产品契约，不列入 `update_page_xml` 等 advanced/低层工具；`update_hierarchy_xml` 已不属于任何生产 profile。矩阵以一行一个操作为原则；“工具英文名”统一使用真实 typed MCP 工具名。通配符 `*` 只表示 Notebook、SectionGroup、Section、Page 对象层级及工具名所需的单复数形式；不同动作名不得由 `*` 代替，应在同一单元格中逐项列出。

| 类别  | 操作               | 工具英文名                                                                           | Notebook                   | SectionGroup            | Section              | Page                             | 备注                                                                                              |
| ----- | ------------------ | ------------------------------------------------------------------------------------ | -------------------------- | ----------------------- | -------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------- |
| `C` | 创建               | `create_*`                                                                         | `T`                      | `T`                   | `T`                | `T`                            | —                                                                                                |
| `R` | 列出               | 当前：`list_*`；目标：由无过滤 `query_*` 分页取代                                  | `T`：当前注册           | `T`：当前注册        | `T`：当前注册     | `T`：当前注册                 | TODO 022 的 Query 验证完成并经用户明确批准后，五个 List 将从项目整体移除；当前仍是实现事实。       |
| `R` | 获取元数据         | `get_*`                                                                            | `T`                      | `T`                   | `T`                | `T`                            | —                                                                                                |
| `R` | 查询元数据         | `query_notebook` / `query_section_group` / `query_section` / `query_page` | `T`：固定 open root | `T`：root/Notebook/Group | `T`：root/Notebook/Group | `T`：root/Notebook/Group/Section | 阶段 A 已注册；不读取 Page 正文。 |
| `R` | 搜索正文           | `search_pages`                                                                     | `T`：typed 或全部已打开 scope | `T`：typed scope       | `T`：typed scope    | `T`：返回对象                  | 全局 scope 使用一次 hierarchy 快照和调用级预算。                                                  |
| `R` | 获取父级           | `get_parent`                                                                       | `T`：返回空父级          | `T`                   | `T`                | `T`                            | Page 的容器父级是 Section。                                                                       |
| `R` | 获取路径           | `get_path`                                                                         | `T`                      | `T`                   | `T`                | `T`                            | —                                                                                                |
| `R` | 获取树             | `get_tree`                                                                         | `T`                      | `T`                   | `T`                | `T`：含缩进树                  | Page 缩进树是派生关系，不是容器父子关系。                                                         |
| `R` | 获取 Page 内容     | `get_page_xml` / `get_page_text` / `get_page_objects` / `get_binary_content` | `—`                     | `—`                  | `—`               | `T`                            | XML、文本、内容对象和二进制按需分工具读取；二进制读取需要当前 Page 对象快照中的`callback_id`。  |
| `U` | 重命名             | `rename_*`                                                                         | `X`                      | `T`                   | `T`                | `—`：见 update_page_title    | —                                                                                                |
| `U` | 更新标题           | `update_page_title`                                                                | `—`                     | `—`                  | `—`               | `T`                            | —                                                                                                |
| `U` | 更新正文           | `append_to_page` / `replace_page_body` / `add_image_to_page`                   | `—`                     | `—`                  | `—`               | `T`：append/replace/image      | —                                                                                                |
| `U` | 重新排序           | `reorder_page` / `reorder_section` / `reorder_section_group`（prototype）      | `—`                     | `X`：后端固定名称升序 | `E`                | `T`                            | Page/Section 只在同一容器父级内移动并保持 ID；SectionGroup 不支持排序，遗留入口不构成受支持能力。 |
| `U` | 缩进/取消缩进      | `reorder_page`                                                                     | `—`                     | `—`                  | `—`               | `T`                            | 通过`page_level` 实现。                                                                         |
| `D` | 删除               | `delete_*` / `delete_page_content`                                               | `X`：Close 不等于 Delete | `T`                   | `T`                | `T`：另含内容对象删除          | 此处`delete_*` 只展开为 hierarchy 对象删除工具。                                                |
| `O` | 复制               | `copy_*`                                                                           | `E`                      | `E`                   | `E`                | `E`：复制完整缩进子树          | 可跨允许的目标容器，始终创建新 ID。                                                               |
| `O` | 换父级（Reparent） | `reparent_page` / `reparent_section` / `reparent_section_group`                  | `—`                     | `E`：仅同 Notebook    | `E`：仅同 Notebook | `E`：仅同 Notebook、可重映射 ID | 三类工具共用 Reparent 实验门；Page 返回 Page/内容对象 `id_map`。                                   |
| `O` | 移动（Move）       | `move_page` / `move_section` / `move_section_group`                                | `—`                     | `E`：仅跨 Notebook、新 ID | `E`：仅跨 Notebook、新 ID | `E`：新 ID，验证后非永久删除源 | 容器 Move 完整递归、只删除一次源根；同 Notebook 容器变更使用 Reparent。三类 Move 均有当前环境真实证据。 |
| `O` | 导出               | `publish_object`                                                                   | `T`                      | `X`                   | `T`                | `T`                            | —                                                                                                |
| `O` | 获取超链接         | `get_hyperlink`                                                                    | `T`                      | `T`                   | `T`                | `T`：可定位内容对象            | —                                                                                                |
| `O` | 导航               | `navigate_to` / `navigate_to_url`                                                | `T`                      | `T`                   | `T`                | `T`：可定位内容对象            | —                                                                                                |
| `O` | 同步               | `sync_notebook`                                                                    | `T`                      | `X`                   | `X`                | `X`                            | —                                                                                                |
| `O` | 关闭               | `close_notebook`                                                                   | `T`                      | `—`                  | `—`               | `—`                           | 仅适用于 Notebook。                                                                               |

精确参数、返回字段和环境变量见 [工具参数与返回格式](../design/tool_contracts.md)。矩阵中的 `T/E` 只表示工具和合同存在，不表示每个真实 OneNote 环境均已验证。

## 5. Advanced/低层操作

Advanced profile 用于开发、诊断和受控能力探测，不属于默认 typed 对象模型，也不参与对象—操作矩阵评级。Page 与 SectionGroup Reparent 的既有底层证据已封装到 typed 工具和迁移后的具名场景；advanced profile 不再包含 raw hierarchy mutation。

6 个 advanced 工具的注册条件、逐工具用途、policy 门限以及 raw hierarchy 移除边界，统一由 [Advanced/低层操作](../design/advanced_operations.md) 定义；本评估不再复制该设计合同。

## 6. 已实现的安全与动态契约

### 6.1 工具命名与返回

默认工具已按对象和单一动词组织，例如 `list_section_groups`、`get_section_group`、`rename_section`、`delete_page` 和 `copy_section`。统一响应 envelope 包含 `ok/complete/warnings`；失败使用固定 `code`，并将 validation、policy、partial failure 和 backend error 分开。多步 mutation 可返回 `partial=true`、`completed_steps`、`created_ids` 或明确的 remaining state。

### 6.2 Mutation policy

以下能力相互独立且默认关闭：

- typed Writes；
- Deletes；
- Permanent Deletes；
- 实验 Section Reorder；
- 实验 Page/Section/SectionGroup Reparent；
- 实验 Copy；
- Page Move；
- Section/SectionGroup Move（共用独立容器 Move 开关）；
- raw XML/advanced profile。

注册工具不等于取得执行权限。Permanent Delete 不能替代普通 Delete 开关，Move 需要 Writes、Deletes、Copy 和对应自身开关的完整闭包；Page 与容器 Move 的开关互不替代。SectionGroup Reorder 不属于可授权的实验能力：后端只提供固定名称升序，遗留开关必须保持关闭。默认 typed 工具不暴露 `force`；Notebook Delete 在 typed 和 advanced generic 路径均被拒绝。

### 6.3 Query 与 Search

Metadata Query 与 Page 正文 Search 已分离，但当前 Query 工具面仍未完成 typed 收敛。

当前实现事实：

- 默认 profile 已注册四个 fixed-type Query，旧 `query_hierarchy(resource_type, ...)` 不再注册；
- root Query 分别使用最浅必要的 `hsNotebooks/hsSections/hsPages`，start-node Query 先以一次 root `hsSections` catalog 验证，再对精确 ID 使用目标 scope；
- 它不是逐 Notebook 调用 COM，也没有利用 `FindMeta` 执行复合查询；
- 即使只查询 Notebook 或 Section，也会获取到 Page 层级；
- 当前没有调用方可选起点，且尚未显式排除 `is_open=false` Notebook 及其后代；
- `limit` 只截断过滤后的响应，不减少 COM 快照或 Python 扫描工作量。

[TODO 022](../todo/022_typed_metadata_query_tools_and_native_scopes.md) 已改为以下目标，尚未实施：

| 目标工具 | 公开 scope | 原生 COM 映射 | 固定返回类型 |
| --- | --- | --- | --- |
| `query_notebook` | 固定全部已打开 Notebook | `GetHierarchy("", hsNotebooks)` | Notebook |
| `query_section_group` | 显式 root，或一个 Notebook/SectionGroup ID | `GetHierarchy(start_id, hsSections)` | SectionGroup |
| `query_section` | 显式 root，或一个 Notebook/SectionGroup ID | `GetHierarchy(start_id, hsSections)` | Section |
| `query_page` | 显式 root，或一个 Notebook/SectionGroup/Section ID | `GetHierarchy(start_id, hsPages)` | Page |

四个已实现工具不接受 `resource_type`，不合并多个离散起点，不逐 Notebook 扫描，并使用最浅必要 `HierarchyScope`。Notebook/SectionGroup/Section 使用 `name_equals/name_contains`，Page 使用与 domain 字段一致的 `title_equals/title_contains`，并把直属容器 `section_id` 与派生缩进关系 `parent_page_id` 分开；四工具使用与 `search_pages` 一致的 `offset=0/page_size=200` 参数约束和 `count/total_matches/offset/page_size/has_more/next_offset` envelope，Query 的一致性标记为 `live_hierarchy`。旧通用 Query 已移除，`global_query` 未实施。五个 List 仍等待真实 Query 证据与用户单独批准后才可整体删除；`list_hierarchy(include_xml=true)` 和混合类型单响应不提供替代入口。

Search 具有以下当前边界：

- 必须显式指定 root，或一个精确 Notebook、SectionGroup、Section ID 作为 scope；
- 公开路径固定为 OneNote index，每次调用只执行一次 `FindPages`，没有 `local_scan` 选择或 fallback；
- 过滤后的完整候选集先检查默认 1000 Page 上限，再执行 `offset/page_size` live-index 分页；
- snippet 只 hydration 当前页，并限制单页字符、总字符、总耗时和 snippet 长度；
- 每页重新执行实时索引查询，不承诺跨页冻结快照；
- 当前按字符而不是实际下载字节计量，且 COM 读取为顺序执行，不存在可配置并发数。

因此原审计的“无界本地扫描”问题已经解决，但“总下载字节预算”仍不是当前 Search 合同。

### 6.4 Page 内容修改

`append_to_page`、`add_image_to_page`、`update_page_title` 和内容对象删除均会确认精确 Page 上下文。`replace_page_body` 明确是删除受支持对象后再写入的非原子重建：中途失败返回 partial 状态，不宣称事务或回滚。

当前仍没有独立的 `plan_replace_page_body`。如果未来需要在执行前审查删除对象数、正文变化或不可恢复风险，应另行设计 plan/execute 契约；本报告不把该建议写成现有能力。

## 7. Copy/Move 实现与真实证据

### 7.1 当前实验实现

四层 Copy、Page Move 和跨 Notebook Section/SectionGroup Move 已不再是概念性 P2 路线，而是默认注册、独立门控的实验工具。Move 语义天然包含重建；生产合同与当前环境真实证据共同确认以下编排：

- `plan_copy` 与 execute 工具使用无状态 `plan_digest` 绑定源、目标、选项和预算；
- Copy 预算限制层级对象、Page、内容对象、单页/总 XML 字节以及计划/执行时间；
- Page Copy 默认选择根 Page，可显式选择完整缩进子树，并返回所选范围的 old→new `id_map`；
- 名称冲突拒绝覆盖、合并或自动后缀；
- 未知根节点或未知后代节点产生结构化 issue，不静默透传；
- 运行中失败保留已创建目标和部分失败证据，不执行破坏性猜测回滚；
- Move 只有在每页按对应 tier 验证、拓扑一致且源未变化后，才从叶到根执行 `DeleteHierarchy(permanently=false)`。
- 容器 Move 复用同一 Copy gate，但只对 Section/SectionGroup 源根执行一次 `DeleteHierarchy(permanently=false)`；随后要求计划中的全部源子树 ID 不再活动，并复核目标子树 digest 未在删除阶段变化。

Move 的成功关口是源子树从活动 hierarchy 消失。COM 若能返回 `is_in_recycle_bin=true`，它是正向诊断证据；真实环境已观察到 UI 能显示已删除 Page、但 COM 不再枚举旧 ID，因此缺少回收站标记不能单独判定删除失败。详见 [OneNote COM 回收站可见性](../lesson/onenote_com_recycle_bin_visibility.md)。

### 7.2 证据矩阵

| 能力或结论                                                              | 自动化合同                                                                                                                                  | 用户确认的真实证据                                                                                              | 当前判断                          |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| 五层 typed model、List/Get/Path/Tree                                    | 已覆盖                                                                                                                                      | 不要求 mutation 证据                                                                                            | 当前实现契约                      |
| Typed Metadata Query                                                     | 四层拆分、原生 scope、open-only、严格 schema 与分页合同已有自动化覆盖                                  | `query-metadata-scopes` human-gated 场景及 dry-run 已交付，当前尚无用户真实运行证据                                         | 阶段 A 已实现，真实证据待补    |
| policy、confirmation、partial failure、预算                             | 已覆盖                                                                                                                                      | 真实 mutation 仍按 scenario 分项确认                                                                            | fail-closed 合同成立              |
| `rename` 与 `create` 隔离 Runner 闭环                               | 已覆盖                                                                                                                                      | 2026-08-06 用户运行通过                                                                                         | 已取得指定环境证据                |
| Scenario 独立最小 fixture、单 MCP、lease 与失败保留                     | 已覆盖                                                                                                                                      | 用户完成低风险与严格`copy_only` 运行；单样本 MCP starts 从 2 降为 1                                           | TODO 003 已完成，不外推普遍性能   |
| 严格 Move 的`copy_only/source_deleted=false` 安全门                   | 已覆盖                                                                                                                                      | 修复后真实运行按预期非零退出并保留现场                                                                          | 失败安全门已验证                  |
| `Outline/Image/RichText/Table` Page Copy                              | 已覆盖 strict canonical tier                                                                                                                | 用户在保留现场确认 UI 一致                                                                                      | 已进入保真 allowlist              |
| `List/Tag` Page Copy                                                  | 已覆盖 semantic tier                                                                                                                        | 用户确认 UI 语义一致；COM 会重编号并重排部分 XML                                                                | 已进入保真 allowlist              |
| Page Reorder                                                            | 已覆盖 typed 顺序/缩进与内容不变量                                                                                                          | 用户可通过编号 fixture 直接核对 UI 顺序                                                                         | 已交付 typed 同父级能力           |
| Section Reorder                                                         | 已覆盖两种合法父级、写后顺序与 Page 内容不变量                                                                                              | 用户确认真实 OneNote UI 排序成功；早期 Runner hash 假阳性已定位并修复                                           | 保留 typed 实验门控               |
| SectionGroup Reorder                                                    | 已覆盖 fail-closed 写后回读；诊断场景保留但显式排除于`all`                                                                                | Notebook 直属 Group 的`A,B,C → A,C,B` 请求中，UpdateHierarchy 返回成功但实际仍为固定名称升序；嵌套操作未执行 | 功能受限、验证失败；明确拒绝      |
| Section Reparent                                                        | 已覆盖 Notebook→SectionGroup、SectionGroup→Notebook、SectionGroup→SectionGroup 三种同 Notebook 父级变化，含编号 Page、逐步回读和逆序恢复 | 用户真实运行确认三条路线成功                                                                                    | 保留 typed 实验门控               |
| Page Reparent                                                           | typed service 与 runner 已覆盖精确 confirmation、同 Notebook、Page/内容对象 ID 一对一重映射、Tag-index 归一化富内容、无关对象和逻辑恢复 | 用户确认迁移后的 `reparent_page` 场景在当前环境真实通过                                                       | typed 实验工具；仍不进入`all`   |
| SectionGroup Reparent                                                   | typed service 与 runner 已覆盖防循环、三种父级路线、后代 ID/拓扑、Page 内容、无关对象和逆序恢复                                         | 用户确认迁移后的 `reparent_section_group` 场景在当前环境真实通过                                              | typed 实验工具；仍不进入`all`   |
| Page/Section/SectionGroup/Notebook Copy 与 Page Move                    | 已覆盖 root-only/subtree、递归容器、Runner 与生产合同                                                                                       | 用户已确认四层 Copy、默认单页/完整子树 Page Copy；`run-2026-08-11-20-29-19` 再确认 Page Move 两种范围、保留后代和非永久删除 | 当前环境真实闭环已完成            |
| 跨 Notebook Section/SectionGroup Move                                  | 已覆盖专属 digest、独立 policy、完整单射映射、源重校验、单次根删除、源子树 inactive、目标复核和双 Notebook 场景                           | `run-2026-08-11-20-31-28` 与 `run-2026-08-11-20-33-29` 均由用户运行通过；完整源子树 inactive，双 role lease 关闭 | 当前环境真实闭环已完成            |
| InkDrawing/UIShape/MediaFile/InsertedFile Copy；FileAttachment/MeetingInfo/Embedded Spreadsheet 保留边界 | 四种已验证类型具备 detector、生产 comparator 和 fail-closed XML 分支；Shape 保持公开 `kind=InkDrawing`；InsertedFile 使用可读本地 source/cache 路径重建；Embedded Spreadsheet 尚无公开表示证据 | 四种类型的用户隔离 Copy 已通过；MediaFile 覆盖同/跨 Section，InsertedFile 另由用户打开目标附件确认合成内容一致；Embedded Spreadsheet 未执行真实 backend run | 四种已进入保真 allowlist；FileAttachment/MeetingInfo/Embedded Spreadsheet 保持 unverified/unsupported |

`Outline/Image/RichText/Table/List/Tag/DisplayEquation/InkDrawing/UIShape/MediaFile/InsertedFile` 的证据只适用于已记录的真实环境与验证 tier，不应改写为 OneNote COM 的跨版本普遍保证。

## 8. 剩余缺口与下一阶段路线

### 8.1 当前跟踪状态

| TODO                                                                                                        | 状态   | 与本报告的关系                                                      |
| ----------------------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------- |
| [001：本地程序化 OneNote 隔离验证 Runner](../todo/001_programmatic_isolated_mutation_runner.md)              | 已完成 | Runner 架构、纯合同与用户确认的逐 scenario 真实验收矩阵均已闭环     |
| [002：P2 四层 Copy 与 Page Move](../todo/002_p2_copy_and_reconstructive_page_move.md)                        | 已完成 | 用户确认四层 Copy 与最终 Page Move 的五个统一 fixture 场景全部通过  |
| [003：Scenario 独立 Fixture 与单 MCP 进程闭环](../todo/003_scenario_scoped_mcp_and_fixtures.md)              | 已完成 | 架构、合同、低风险运行、性能单样本和严格失败门已有证据              |
| [004：交互式 Copy 未验证内容保真验收](../todo/004_interactive_copy_move_content_fidelity_validation.md) | 已完成 | InkDrawing、UI Shape、MediaFile 的逐类型 Copy 证据、生产 comparator 和静态 allowlist 已闭合；Move 复用 Copy 类别门禁，FileAttachment/MeetingInfo/Embedded Spreadsheet 已排除 |
| [005：Page Copy 默认仅复制单页，可选包含缩进子树](../todo/005_page_copy_without_indentation_subtree.md)      | 已完成 | 默认单页与显式完整子树已交付，用户已确认双 case 真实验收通过       |
| [006：Typed Section 与 SectionGroup Reorder](../todo/006_typed_section_and_section_group_reorder.md)         | 已完成 | Section 真实排序已确认；SectionGroup 因后端固定名称升序而明确拒绝   |
| [007：跨版本兼容性证据与环境元数据](../todo/007_cross_version_compatibility_evidence.md)                     | 待办   | 后续补充非阻塞的跨版本证据，不重开 SectionGroup Reorder 能力结论    |
| [008：全部已打开 Notebook 的全局 Page 搜索](../todo/008_all_open_notebooks_search_scope.md)                  | 进行中 | 实现、纯合同和 fresh-only 双 Notebook scenario 已交付；等待用户真实运行确认       |
| [009：Typed Reparent 工具与隐藏 Raw Hierarchy XML](../todo/009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md) | 已完成 | typed 工具、生产隐藏和纯合同已交付；用户确认三个迁移场景全部通过    |
| [010：Manual Validation Dry-run 自动测试用例注册](../todo/010_registered_dry_run_test_cases.md)              | 已完成 | registry case、正式 parser 与零副作用 sentinel 合同已交付           |
| [011：Scenario 自管理 Fixture Recipe](../todo/011_scenario_owned_fixture_recipes.md)                         | 已完成 | Scenario-owned recipe、增量 recorder 和共享 typed primitive 已交付  |
| [012：跨 Notebook 容器重建式 Move](../todo/012_reconstructive_section_and_section_group_move.md)            | 已完成 | 四个 typed 工具、独立门控与双 Notebook 场景已交付；用户确认 Section/SectionGroup 真实 Move 均通过 |
| [013：Reparent Page 子树范围与 Mutation 目标位置回传合同](../todo/013_reparent_default_placement_contract.md) | 阻塞 | 实现与 dry-run 已交付；受 HUMAN-GATED 门限阻塞，等待新范围场景及十个既有位置场景的用户真实证据 |
| [018：在线视频表示与 Copy 保真验证](../todo/018_online_video_copy_fidelity_validation.md)                  | 已取消 | 不建立独立对象类型或有损 Copy 合同；局限性证据保留在 Lesson |
| [022：四层 Typed Metadata Query、原生 Scope 与 List 工具退役](../todo/022_typed_metadata_query_tools_and_native_scopes.md) | 阻塞 | 四 Query、原生 scope、纯合同和完整双 Notebook 场景已交付；尚无用户真实场景 artifact，取得证据和后续独立批准前不能移除五个 `list_*`。 |

### 8.2 优先事项

1. 由用户运行 TODO 022 的 `query-metadata-scopes`，审查 root/三类起点、open-only、缩进父页和分页证据；证据通过后再单独决定是否批准 List 退役。
2. 完成 TODO 008 的双 Notebook index-only 真实验收，核对归属、scope、预算与 index readiness，不把空 `start_id` 未经证据地等同于 Desktop `Ctrl+E`。
3. 由用户运行 TODO 013 的 `reparent-page-scope` 与十个既有位置场景，核对保存证据后完成状态收敛。
4. 保持 TODO 004 已完成的静态边界：InkDrawing、UIShape、MediaFile、InsertedFile 的 Copy comparator、用户 UI verdict 和 allowlist 评审已经完成，运行时输入不得动态扩展生产 allowlist，Move 不另建逐类别门禁。
5. 继续保持统一 Reparent、Copy、Page Move 和容器 Move 的独立实验开关；SectionGroup Reorder 的遗留开关保持关闭。若 Page body replacement 的审查需求提高，再为 Replace 设计独立 plan/execute。只有在收集正式基准后才评估长驻单线程 COM broker。

## 9. 最终判断

原审计的架构取舍已经实施：项目现在是 local-only、COM-first、typed-object-first 的 MCP，而不是把 COM 方法和 raw XML 直接当成产品模型。P0/P1 的主要对象、安全和 mutation 边界已有代码与自动化合同，README 中模糊的“Full CRUD”也已被具体能力目录取代。Metadata Query 已按四种对象拆分并采用原生起点、open-only 和 Agent 可发现合同；TODO 022 剩余门限是真实场景证据与其后的 List 退役单独批准。

下一阶段不需要再次设计一套对象模型。四层 Copy、默认单页/完整子树 Page Copy、更新后的 Page Move 和两个跨 Notebook 容器 Move 都已由用户确认完成当前环境真实闭环；全局搜索仍需独立真实验收。Reparent/Copy/Move 的目标根位置回传和新的 Reparent Page 范围实现已交付，但仍等待用户运行真实场景确认后端证据。墨迹、UI 形状和录像 MediaFile 的可审查保真比较及静态 allowlist 已完成，Move 统一复用 Copy 类别门禁。FileAttachment、MeetingInfo 与 Embedded Spreadsheet 的排除原因见 [`lesson/copy_content_type_exclusions.md`](../lesson/copy_content_type_exclusions.md)。

仍应坚持的长期边界包括：Notebook Delete 不受支持，Close 不等于 Delete；SectionGroup 只按名称固定升序，Reorder 请求必须拒绝；路径只用于展示和只读解析；Replace 与递归 Copy/Move 是多步、非原子操作；raw XML 不能进入默认工具面；真实 OneNote mutation 只能由用户通过具名、隔离、最小权限的 scenario 显式启动。
