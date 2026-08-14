# 013：Reparent Page 子树范围与 Mutation 目标位置回传合同

> ID：013
> 状态：已完成
> 优先级：P2
> 类型：公开 mutation 契约 / Reparent Page 范围与目标位置可观测性
> 更新日期：2026-08-14

## 决策摘要

Reparent、Copy 与 Move 的执行工具应在既有成功响应中统一增加 `destination_position`，报告本次操作产生或迁移的目标根对象在其有效父级中的**执行后观察位置**。

`reparent_page` 同时新增与 `copy_page` / `move_page` 一致的 `include_descendants=false` 参数：默认只 Reparent 选中的 Page，显式为 `true` 时 Reparent 完整缩进子树。默认 root-only 路线对被排除后代的处理与 `move_page` 一致：先将完整后代子树整体提升一级并验证，再迁移选中 Page。

Page Copy、Move 与扩展后的 Reparent 在 destination 中都把选中 Page 作为目标根 Page。即使 `include_descendants=true`，`destination_position` 也只描述该目标根 Page 在目标 Section 完整扁平 Page 序列中的位置，不为本次子树的每个缩进后代返回位置，也不返回 `page_level`。

本 TODO 不再要求 Reparent 固化 `append_last` 默认落点，也不要求 Copy/Move 为统一落点增加额外 mutation。工具继续只承担各自原有语义：

- Reparent 只表示同一 Notebook 内换父级；Page 可选择 root-only 或完整缩进子树范围，并继续报告适用的原生 ID remap；
- Copy 只创建并验证目标副本；
- Move 只执行既有的 Copy→验证→非永久删除源流程；
- 位置字段只投影已经发生的最终状态，不是位置请求、位置保证或隐式 Reorder。

调用方若需要指定位置，仍应显式调用已有 `reorder_page` / `reorder_section`。SectionGroup 没有可用的 Reorder，工具只报告后端实际顺序，不模拟控制能力。

## 原始背景与问题

实施前，三个 `reparent_*` 工具只明确目标父级；四个 `copy_*` 和三个 `move_*` 执行工具虽然返回 fresh target `item` / `id_map`，也没有统一告诉 Agent 目标根最终位于父级中的哪个位置。调用方若关心落点，只能再次枚举 hierarchy 并自行解释 Page `order` 与容器 child traversal。当时 `reparent_page` 还只接受没有 parent/child Page 的根 Page，范围能力与已经支持 `include_descendants` 的 Page Copy/Move 不一致。

此前方案试图把 Page/Section Reparent 统一固化为 `append_last`，并在失败时围绕该预期建立 placement verification。该方案把“换父级”和“控制顺序”重新耦合：不同对象的后端排序能力不对称，SectionGroup 只有固定名称顺序，而且为了制造统一末位还可能诱发隐藏 Reorder、额外 policy、第二次 mutation 和更复杂的部分失败。

新的合同只解决可观测性：执行工具在现有有界 read-back 中定位 fresh target，并返回同一快照里的位置。位置与预期不同本身不是失败；无法在已有成功门要求的最终 hierarchy 中可靠定位目标，才属于响应合同未完成或既有 topology read-back 失败。

## 工具范围

| 家族 | 纳入的执行工具 | 位置观察时点 |
| --- | --- | --- |
| Reparent | `reparent_page`、`reparent_section`、`reparent_section_group` | Reparent mutation 后、fresh ID 与目标父级验证完成的同一最终快照；Page 新增可选子树范围 |
| Copy | `copy_page`、`copy_section`、`copy_section_group` | Copy 目标身份、拓扑和内容 read-back 完成的最终快照 |
| Copy Notebook | `copy_notebook` | 返回 `not_applicable`；Notebook 在公开 OneNote hierarchy 中没有可报告的父级 sibling 位置 |
| Move | `move_page`、`move_section`、`move_section_group` | 源删除及最终目标复核完成后的快照；不得直接复用删除前 Copy 阶段的索引 |

`plan_copy`、`plan_move_page`、`plan_move_section` 和 `plan_move_section_group` 不在范围内。Plan 阶段尚无 fresh destination target，只继续返回计划绑定的 destination snapshot；不得虚构预测位置或把预测索引加入 mutation 语义。

本 TODO 只报告每个执行结果顶层 `item` 所代表的**目标根对象**。即使 Page Reparent/Copy/Move 使用 `include_descendants=true`，缩进后代也只由 `id_map` 和既有 topology evidence 描述，不为整个子树返回位置列表。

## 统一响应形式

字段放在响应顶层，与 `item`、`copy_report`、`id_map` 等既有字段并列。它不放入 `copy_report`，因为位置是 Reparent/Copy/Move 共有的 hierarchy 事实，不是内容保真或 Copy acceptance 结论。

Page 示例：

```json
{
  "item": {"id": "fresh-page-id", "resource_type": "page"},
  "destination_position": {
    "status": "observed",
    "resource_type": "page",
    "parent_id": "destination-section-id",
    "parent_type": "section",
    "sibling_scope": "section_page_sequence",
    "index": 3,
    "sibling_count": 4,
    "sequence_source": "page_order"
  }
}
```

Section 或 SectionGroup 示例：

```json
{
  "item": {"id": "fresh-or-preserved-id", "resource_type": "section"},
  "destination_position": {
    "status": "observed",
    "resource_type": "section",
    "parent_id": "destination-parent-id",
    "parent_type": "notebook",
    "sibling_scope": "same_type_direct_children",
    "index": 2,
    "sibling_count": 5,
    "sequence_source": "hierarchy_child_order"
  }
}
```

Notebook Copy 示例：

```json
{
  "item": {"id": "fresh-notebook-id", "resource_type": "notebook"},
  "destination_position": {
    "status": "not_applicable",
    "resource_type": "notebook",
    "reason": "notebook_has_no_hierarchy_parent"
  }
}
```

### 字段合同

- `status`：正常 hierarchy child 为 `observed`；Notebook 为 `not_applicable`。只有已经发生 mutation 的 partial failure 才允许 `unavailable`，并必须同时返回稳定的 `reason` code。
- `resource_type`：必须与顶层 `item.resource_type` 一致。
- `parent_id` / `parent_type`：来自最终 read-back，不按请求参数回显。Page 的目标根 container parent 是最终 Section。
- `sibling_scope="section_page_sequence"`：只用于 Page；`index` 在最终 Section 按 `order` 排列的完整扁平 Page 序列中计算，序列包含根 Page 与缩进 Page。即使操作包含子树，响应也只为 fresh 目标根计算一份位置，不为纳入的后代返回位置列表。
- `sibling_scope="same_type_direct_children"`：只用于 Section/SectionGroup；`index` 在同一父级的同类型直属子项序列中计算，两种容器不混成一个索引空间。
- `index`：零基索引；必须满足 `0 <= index < sibling_count`。对 Page，它表示目标根 Page 在 Section 完整扁平 Page 序列中的位置。
- `sibling_count`：与 `index` 来自同一次有界 read-back。调用方可由两者判断当时是否为首项或末项；响应不再重复返回 `is_first` / `is_last`。
- `sequence_source="page_order"`：Page 使用 parser 已验证的扁平 `order` 建立完整 Section 序列，并以 fresh 目标根在该序列中的零基下标作为 `index`。
- `sequence_source="hierarchy_child_order"`：Section/SectionGroup 使用最终 hierarchy 中该父级直属、同类型 child 的遍历序列。它表示后端观察顺序，不表示调用方可控制的排序能力。

Page 的 `destination_position` 不返回 `page_level` 或 `parent_page_id`。三个 Page 工具的顶层目标在 destination 中均为根 Page，位置响应只需报告该对象在完整 Section Page 序列中的 index；若包含子树，后代 level/parent 属于独立的 subtree topology 验证与 `id_map` 证据，而不是目标根位置字段。

`destination_position` 不返回 `strategy`、`expected_placement`、`predecessor_id` 或 placement policy。调用方不能把 `index` 当成长期稳定句柄；如需后续 Reorder，应重新读取当前 siblings，并使用 Reorder 自身的 typed confirmation 和 policy。

## 位置语义边界

### Page

Page 的后端具有显式扁平 `order`，缩进由 `page_level` 和派生 `parent_page_id` 另行表达。位置 projector 必须使用最终 fresh 目标根 ID，在最终 Section 的完整 Page 序列中找到它并验证它是根 Page，再直接以该序列下标计算 index。不能用旧 Page ID、创建循环中的临时序号或请求中的目标 Section ID 拼装响应。

### `reparent_page` 的范围合同

公开 schema 新增 `include_descendants: bool = false`，不新增 `page_level` 或位置输入：

- 选中 Page 可以原本是根 Page 或缩进 Page，也可以带有缩进后代；“root-only”表示只处理所选范围的根对象，不要求它在源 Section 中原本为 level 1。所选 Page 在 destination 中始终归一化为目标根 Page。
- 省略或显式 `include_descendants=false` 时，只 Reparent 选中 Page。与 root-only `move_page` 一致，所有被排除后代留在源 Section，并在 Reparent 前将完整后代子树整体提升一级；保持精确 ID、Section、扁平顺序、相对层级和内容。提升与回读不完整时不得尝试 Reparent。
- 显式 `include_descendants=true` 时，Reparent 选中 Page 及其完整缩进子树；目标根归一化为 level 1，后代保持相对源根的顺序和相对 level，并验证完整 topology/content。
- `include_descendants` 是 Reparent execute 的直接 confirmation 范围，不引入额外 plan 工具；service 必须在 mutation 前有界捕获并绑定选中范围、被排除后代和目标状态。
- Page 及可观测内容对象仍可能由 OneNote 重映射 ID。`id_map` 必须覆盖实际 Reparent 范围：root-only 至少覆盖目标根；subtree 路线覆盖根与全部纳入后代，并保持一对一、完整、单射。被排除并提升的后代保持原 ID，不进入 Reparent `id_map`。

该参数改变的是 Reparent 范围与源侧后代处理，不是位置控制。目标根的 `destination_position` 在两种路线中使用完全相同的结构；`include_descendants=true` 不扩展为后代位置列表。

现有 Page Copy/Move 为保持选择范围的相对拓扑，继续执行其已经公开并验证的排序步骤；本 TODO 不撤销这些语义，也不把“统一追加末位”扩展为全部 Reparent/Copy/Move 家族的新保证。

### Section

Section 没有 Page 那样的显式 `order` 字段。返回值只承诺它在最终 hierarchy read-back 的直属 Section child 序列中的观察索引。实施前必须用现有 `reorder_section` 与 manual-validation 证据交叉确认该 traversal 对 Agent 有稳定、可解释的意义；不能把 XML 构造顺序单独当成事实。

### SectionGroup

当前后端把 SectionGroup 暴露为固定名称升序，且项目已明确拒绝 `reorder_section_group`。`destination_position` 仍可返回 read-back 中的同类型索引，但 tool 描述必须明确该索引来自后端排序，仅为快照，不代表 Reparent/Copy/Move 控制了位置。

### Notebook

`copy_notebook` 的 destination 是本地目录与一个新打开的 Notebook，不是 hierarchy parent。文件系统目录中的名称排序、OneNote UI 的 Notebook 展示顺序和 COM 返回顺序都不能冒充父级 sibling 位置，因此固定返回 `status="not_applicable"`。`destination_path` 继续留在既有响应中。

## 成功、部分失败与并发

- 对 Page、Section、SectionGroup，普通成功响应必须包含 `status="observed"`，且位置与用于最终 topology 验证的同一 read-back 一致。
- `copy_notebook` 普通成功必须包含稳定的 `not_applicable` 对象；不得省略字段或返回伪索引。
- Copy partial / Move `copy_only` 若已精确解析 fresh target，应尽可能返回当次 evidence snapshot 的 `observed` 位置。
- mutation 已发生但目标位置无法可靠读取时，partial details 返回 `status="unavailable"` 和稳定 `reason`；不得猜索引、回显计划索引或为了取得位置自动重试 mutation。
- Move 的成功位置必须在源删除和最终目标复核之后重新计算。同父级 Page Move 中，删除旧源可能使目标索引前移，因此 Copy 阶段位置不能直接提升为 Move 最终位置。
- root-only Reparent Page 是“提升被排除后代→验证提升→Reparent 目标根→验证目标与保留后代”的两阶段、非原子 mutation。提升前失败必须零 mutation；提升已完成但 Reparent 未完成时返回 `outcome="descendants_promoted_reparent_not_completed"`，精确报告保留后代当前 topology、`reparent_attempted` 和目标根当前父级，不得自动回缩进、再次 Reparent 或伪报成功。
- subtree Reparent 若只迁移了部分目标 Page 或无法得到完整单射 `id_map`，返回独立的 `outcome="reparent_subtree_incomplete"`、已观察到的 source/destination Page IDs、当前父级和 manual recovery evidence；不得继续猜测剩余 Page 或自动回滚。
- 只允许在既有预算内重试 read-back。响应是返回前最后一个已验证快照，不保证用户或并发操作随后不会改变 siblings。
- 观察位置与某个预设值不同不触发自动回滚、Reorder 或失败；目标身份、父级、内容、删除安全等既有 invariant 仍独立执行。

## Tool 描述要求

十个执行工具的公开描述都应说明：成功时返回 `destination_position`，它是执行后 read-back 中的观察位置，不接受位置参数，也不保证固定首位/末位。

此外：

- `reparent_page` 必须继续提示 Page 可能发生 ID remap，应从 fresh `item.id` / `id_map` 继续操作；
- `reparent_page` 必须说明 `include_descendants=false` 默认只换父级目标根并提升保留后代，`true` 换父级完整缩进子树；目标位置始终只描述目标根 Page；
- `reparent_section` 与 Page/Section 的 Copy/Move 可提示自定义位置由独立 Reorder 完成，但不得暗示两次调用原子；
- `reparent_section_group`、`copy_section_group`、`move_section_group` 必须说明 SectionGroup 顺序由后端固定名称排序决定；
- `copy_notebook` 明确返回 `not_applicable`，而不是声称 Notebook 位于父级索引；
- Plan 工具不承诺或预测最终位置。

## 已采用的实现方案

1. 在 service/domain 边界增加只读、typed 的 destination position projector；输入是最终目标根、其父级和一次有界 hierarchy snapshot，不接受 raw XML、名称 selector 或 mutation callback。
2. Page projector 使用显式 `order` 建立 Section 的完整扁平 Page 序列，验证 fresh 目标根只出现一次且为根 Page，并从同一最终 snapshot 返回该对象的 `index` 与完整序列 `sibling_count`；不返回 Page level 或后代位置。
3. 容器 projector 使用父级直属、同类型 child traversal；目标缺失、重复、父级不一致或序列不完整时 fail closed。
4. 为 `reparent_page` 增加 `include_descendants=false`，在 mutation 前有界捕获完整源缩进子树、目标 Section 和无关对象；未知/越界/并发变化必须在首次 mutation 前 fail closed。
5. root-only 路线复用 `move_page` 已验证的保留后代提升 primitive，但将其收敛为内部 typed helper：完整子树整体减一级、保持 IDs/Section/顺序/相对层级/内容，回读成功后才调用 Reparent。helper 不注册为公共 Reorder 或 raw hierarchy tool。
6. subtree 路线构造受限的完整 Page 子树 Reparent update，或采用经 probe 证明等价的 typed 编排；目标根归一化为 level 1，后代按相对 level 恢复。不得逐页无验证地循环或暴露 hierarchy XML。
7. Reparent 在完整 fresh ID mapping 和既有 topology/content read-back 验证完成后构造根位置；不验证预设末位。
8. Copy 在 `_execute_copy()` 的最终 target root 快照上构造字段，并放在顶层；`copy_report` 的内容保真合同保持不变。
9. Move 在完成源删除与最终 destination revalidation 后重新构造字段；partial outcome 使用当时最后一个可信快照，不能无条件复用 nested Copy 结果。
10. Notebook Copy 通过固定 builder 返回 `not_applicable`，不枚举文件系统或全局 Notebook UI 顺序。
11. Tool adapter 只补 `reparent_page.include_descendants`、描述和响应类型；不新增位置输入、generic placement tool、内部 XML 入口或 policy。

## 自动化合同要求

- 三个 `reparent_*`、四个 `copy_*`、三个 `move_*` 成功响应都包含结构正确的 `destination_position`；除 `reparent_page` 新增 `include_descendants=false` 外，现有输入 schema 不变。
- 对新增返回字段的测试不能只断言字段存在或照抄 service 结果。测试 fixture 必须保留一份独立的最终 hierarchy snapshot，用只读 expected-position helper 从 fresh target ID、最终 parent 和最终 sibling sequence 计算 expected object，再与 tool response 逐字段深比较；该 helper 不得调用生产 position builder。
- 十个执行工具均需至少覆盖一个非空 destination 的成功 case；空/单项 destination、目标位于首/中/末位和 ID remap 由相关类型的聚焦测试补齐。字段多余、字段缺失、错误 `status/resource_type/parent_id/parent_type/sibling_scope/index/sibling_count/sequence_source` 都必须失败。
- `plan_copy` 与三个 `plan_move_*` 不返回伪造的最终位置。
- Page 位置覆盖空父级、多个根 Page与缩进 Page、ID remap、同父级 Move 删除后索引变化，以及目标根在完整 Section Page 序列中的 `index/sibling_count` 与最终快照一致；响应 schema 明确不要求 `page_level` / `parent_page_id`。
- `reparent_page` 默认值和显式 `false` 等价；目标没有后代时只执行 Reparent，不产生空 promotion mutation。
- root-only Reparent 覆盖多层、多分支缩进子树：被排除后代整体提升一级并保持精确 ID、Section、扁平顺序、相对层级和稳定内容；只有选中根进入 destination 与 `id_map`。
- subtree Reparent 覆盖完整多层子树：目标根及全部后代进入 destination，完整单射 `id_map`、相对顺序/level/derived parent 和 Page 内容均通过；`destination_position` 仍只有一份并指向 fresh 目标根。
- 所选 Page 原本为缩进 Page时也必须覆盖；范围越界、目标变化、提升前 snapshot 变化均在首次 mutation 前拒绝。目标根必须归一化为 level 1，但该 level 不进入 `destination_position`。
- 覆盖提升调用失败、提升 read-back 失败、提升成功后 Reparent 调用失败、Reparent read-back 超时、subtree 部分迁移和 ID remap 不完整；每类 partial outcome 都精确区分源/目标当前状态且不自动补偿。
- Section 覆盖 Notebook/SectionGroup 两种父级；SectionGroup 覆盖 Notebook/SectionGroup 两种父级及固定名称排序后的观察索引。
- Copy/Move 使用 fresh target root ID；不得把 source ID、分配但未解析的 ID 或名称匹配结果用于位置计算。
- `copy_notebook` 始终返回 `not_applicable`，且保留既有 `destination_path`。
- 同类型 sibling 作用域、零基索引、空/单项/多项父级、目标重复/缺失和 hierarchy traversal 不完整都有纯测试。
- 位置不同于末位不会单独失败，也不会调用 Reorder；mock 必须断言 Reparent/Copy/Move 路径没有为位置报告增加额外 mutation。
- partial failure 在有可信 target snapshot 时返回 `observed`，否则返回带稳定 reason 的 `unavailable`，不猜测位置。
- tool descriptions、响应模型、错误 envelope 和 MCP 注册合同同步覆盖新增字段。

### 自动化位置验证矩阵

| 工具 | 必须使用的 expected snapshot / 特殊断言 |
| --- | --- |
| `reparent_page` | 最终 destination Section 的完整 Page 序列；默认 root-only 与 subtree 均只对 fresh 目标根返回一份位置，覆盖 Page ID remap。 |
| `reparent_section` | 最终目标 Notebook/SectionGroup 的直属 Section 序列；两种 parent type 都覆盖。 |
| `reparent_section_group` | 最终目标 Notebook/SectionGroup 的直属 SectionGroup 序列；expected 必须按 read-back 的后端固定名称顺序计算。 |
| `copy_page` | Copy 完成后的 destination Section 序列；root-only 与 subtree 都只核对 fresh 目标根位置。 |
| `copy_section` | Copy 完成后的目标父级直属 Section 序列；使用 `id_map[source_root_id]`。 |
| `copy_section_group` | Copy 完成后的目标父级直属 SectionGroup 序列；使用 fresh 映射根，并接受后端固定排序产生的 index。 |
| `copy_notebook` | 精确深比较固定 `status="not_applicable"`、`resource_type="notebook"`、`reason="notebook_has_no_hierarchy_parent"`；不得出现 index/count/parent。 |
| `move_page` | 源删除、保留后代提升及最终 destination revalidation 后的 Section 序列；不得复用 nested Copy response 的旧位置。 |
| `move_section` | 源根非永久删除并完成最终 destination revalidation 后的直属 Section 序列。 |
| `move_section_group` | 源根非永久删除并完成最终 destination revalidation 后的直属 SectionGroup 固定排序序列。 |

成功响应测试之外，Copy/Move/Reparent 各家族至少有一个 post-mutation partial case验证可定位目标时返回 `status="observed"`，以及一个目标状态无法可靠读取时返回 `status="unavailable"` + 稳定 reason。mutation 前拒绝不生成伪造的 `destination_position`。

## Manual-validation 方案

位置响应继续扩展现有十个具名执行场景；此外，为 Reparent Page 新范围能力新增独立、HUMAN-GATED 的 `reparent-page-with-level` 场景。使用独立场景而不是把新 mutation matrix 塞入既有 `reparent-page`，原因是 root-only 路线会永久改变 disposable source fixture 的缩进拓扑，不能依赖旧场景的简单反向 Reparent 恢复合同。

位置覆盖的现有场景为：

- `reparent-page`、`reparent-section`、`reparent-section-group`；
- `copy-page`、`copy-section`、`copy-section-group`、`copy-notebook`；
- `move-page`、`move-section`、`move-section-group`。

每个 hierarchy child 场景在 destination 准备至少两个可区分的同类型 anchors。Runner 必须先持久化 mutation response 与 after snapshot，再由独立的 manual-validation helper 仅从 after evidence、fresh target ID 和 manifest-bound parent 计算 expected position，写入单独的 `destination-position-evidence*.json`，最后与 response 逐字段比较。该 helper 不得导入或调用生产 position builder，也不得由 tool response 反向生成 expected evidence。

Page 在最终完整扁平 Section Page 序列中核对 fresh 目标根的 index/count，不要求位置响应回传 level，也不为本次子树后代核对响应位置；缩进子树的 level/parent 继续作为操作自身的 topology invariant 单独验证。Section/SectionGroup 将 hierarchy traversal 与 UI 顺序证据交叉记录。Notebook Copy 独立断言固定 `not_applicable` 对象与既有 destination identity/path。

### 手动位置验证矩阵

| 既有场景 | 必须新增的位置证据 |
| --- | --- |
| `reparent-page` | 正向 Reparent 后按 fresh Page ID 计算目标根位置；默认恢复路线执行后不复用正向 index。 |
| `reparent-section` | 三条合法路线每一步均从各自 after snapshot 计算 Section index/count。 |
| `reparent-section-group` | 三条合法路线均记录固定名称排序后的 Group index/count，并与 UI 顺序证据交叉核对。 |
| `copy-page` | 六个既有 root-only/subtree × destination case 分别计算 fresh 目标根位置；subtree 后代不产生位置数组。 |
| `copy-section` | Notebook 内与跨 Notebook 两个 case 分别计算 fresh Section 根位置。 |
| `copy-section-group` | 两个 case 分别按最终固定名称顺序计算 fresh Group 根位置。 |
| `copy-notebook` | 验证 `not_applicable` 精确形状，不计算文件系统或 UI Notebook index。 |
| `move-page` | 两个 scope case 均只使用源删除后的最终 after snapshot；root-only 保留后代提升完成后再计算。 |
| `move-section` | 使用源子树活动态缺席且目标最终复核完成后的 snapshot 计算 Section 位置。 |
| `move-section-group` | 使用源子树活动态缺席且目标最终复核完成后的固定名称排序 snapshot。 |

任何 response/evidence 字段不一致都必须使场景非零退出并按现有失败规则保持 working bundle 与全部 evidence。UI 顺序检查只作为真实后端交叉证据，不能替代机器逐字段比较。

### 新场景：`reparent-page-with-level`

场景使用一个由本次 run 创建或从不可变模板物理复制的 disposable Notebook、单个前台 MCP 进程、现有 Writes + Reparent 最小权限以及精确 manifest IDs。一次运行包含两个相互独立的 fixture 分支：

1. `root-only-default`：调用时省略 `include_descendants`。源 Section 中所选 Page 初始为 level 2，带一个 level-3 后代，并具有源父页、同级前后 anchors；destination Section 准备至少两个根 Page anchors。断言只有 fresh 所选 Page 进入 destination 并成为根 Page；排除后代留在源 Section 并提升为 level 2，ID、稳定内容和相对位置不变；所选 Page 原父页及其他 anchors 不变；`id_map` 不含排除后代；独立 position helper 从 after evidence 计算目标根 index/count 并与 `destination_position` 深比较，响应不得含 level 或后代位置。
2. `full-subtree`：显式提交 `include_descendants=true`。使用另一棵由 level-2 所选根和两个 level-3 直属后代组成的分支 Page 子树；断言所选 Page 及全部后代迁入 destination，完整单射 `id_map`、目标根归一化为 level 1、后代相对 level/顺序/derived parent、富内容与源父页/无关 anchors 均保持；独立 position helper 仍只对 fresh 目标根生成一份 expected object 并与响应深比较，响应不含 level 或后代位置数组。Fixture 不再构造 OneNote Desktop 不支持的 level 4；该限制不削弱 root-only promotion 或 full-subtree branching 的验收目标。

两个 case 必须分别保存 before、调用参数、mutation response、after、独立计算的位置与内容/topology evidence。不要在真实 OneNote 场景中故意注入“提升成功后 Reparent 失败”；该 partial 状态由纯自动化 fault injection 覆盖。真实场景若自然失败，则立即 fail closed、保留已创建的 working Notebook 和 evidence，不继续第二个 case 或尝试自动恢复。

由于 root-only 成功会有意提升保留后代，新场景在自身静态最小权限下属于不可自动恢复场景：采用“验证最终预期状态后精确关闭 disposable lease”的非恢复式成功生命周期，不增加 Page Reorder 权限来重建初始缩进，也不删除 `.one` 文件。Cache template 必须保持未打开且 byte-for-byte 不变；working copy 属于本 run 并按现有证据/clear 规则保留。任一 mutation、read-back 或 lifecycle 状态不确定时必须非零退出、保持 working Notebook 打开并保存 handoff evidence。场景最初 `included_in_all=false`；用户完成 fresh 与 cache-backed 双 case 真实验证并明确批准后，现已显式纳入 `all`。

场景必须注册 Scenario-owned recipe、静态最小权限和正式 dry-run case。Agent 只允许运行：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py reparent-page-with-level --dry-run --json
```

Move 必须使用删除源后的最终 after snapshot。若 Page Move 支持同父级合法路线，至少一个 case 应证明删除源导致的索引变化不会留下 Copy 阶段旧值。场景只验证“返回值准确描述观察状态”，不要求目标成为末项，也不因非末位而修改现场。

真实 mutation 仍只能由用户显式启动。Agent 只能运行纯测试、mock 和明确带 `--dry-run` 的 manual-validation 命令。

## 风险与缓解

### P1：把观察索引误解为位置保证

字段名、tool 描述和设计文档统一使用“post-operation observed position”；不返回 `strategy` 或 `expected_placement`，不声明 append/prepend，不因索引变化自动 mutation。

### P1：Section traversal 与 UI 顺序不一致

复用 Section Reorder 的既有 parser/service 合同，并在现有人工场景中交叉记录 UI 与 hierarchy。若无法稳定解释，则该类型不能返回普通 `observed` 成功，必须先收敛 `sequence_source` 语义，不能用 XML child 构造意图替代 read-back 事实。

### P1：Move 返回 Copy 阶段旧索引

Move 必须在源删除和最终 destination revalidation 后重新投影；用同父级 Page Move 的索引前移测试锁定该时点。

### P1：root-only Reparent 提升后未完成换父级

被排除后代的提升与目标根 Reparent 是两个有序 mutation，不能伪装成原子操作。首次 mutation 前绑定完整 source/destination snapshot；提升后立即验证保留后代，再执行一次受约束 Reparent。第二阶段失败时保留现场并返回 `descendants_promoted_reparent_not_completed`，不得自动降低后代 level、重复 Reparent 或使用 Reorder 补偿。

### P1：subtree Reparent 的部分迁移或 ID remap

OneNote 可能重映射 Page/内容对象 ID，完整子树又扩大了目标集合。执行后必须从 before/after 集合、父级、相对 order/level、内容摘要构造完整单射 mapping；任何缺失、重复或落在错误父级的 Page 都返回 `reparent_subtree_incomplete` 并保留证据，不按标题或路径猜测。

### P2：Section 与 SectionGroup 混合子项导致索引歧义

统一限定为同父级、同类型直属 siblings；返回 `sibling_scope` 与 `parent_type`，不把混合 XML child offset 暴露为公共位置。

### P2：并发改变 siblings

`index` 与 `sibling_count` 来自同一最终快照并受既有 budget 控制。它们是诊断/决策输入，不是后续 mutation 的稳定 confirmation；调用方在 Reorder 前必须重新读取。

### P2：Notebook 被强行套入层级位置

所有执行工具保持统一字段存在性，但 Notebook 明确 `not_applicable`；不将本地路径、打开顺序或 UI 排序包装成 hierarchy index。

## 依赖与实施顺序

1. 以 [TODO 009](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md) 的 typed Reparent、fresh ID mapping 和 raw XML 隐藏边界为基础。
2. 复用 [TODO 005](005_page_copy_without_indentation_subtree.md) 的 Page 范围命名/default，以及 [TODO 017](017_page_move_selectable_subtree_and_cross_notebook_validation.md) 已验证的 root-only 后代提升、稳定内容比较和 subtree topology 合同。
3. 复用 [TODO 002](002_p2_copy_and_reconstructive_page_move.md) 与 [TODO 012](012_reconstructive_section_and_section_group_move.md) 的 Copy/Move final read-back 和 partial outcome；不改变其保真或删除门限。
4. 复用 [TODO 006](006_typed_section_and_section_group_reorder.md) 已确认的 Section traversal 语义与 SectionGroup 固定名称排序边界。
5. 先实现 Page scope snapshot/promotion/subtree Reparent 纯合同与 fault injection，再实现共享 position projector 并分别接入 Reparent、Copy 和 Move 响应。
6. 注册 `reparent-page-with-level` 的 Scenario-owned recipe、静态权限与 dry-run case，同步 tool descriptions、`docs/design/`、根 README、manual-validation README 与现有场景说明。
7. 由用户单独运行并确认 `reparent-page-with-level` 双 case，再运行受到响应变化影响的既有具名场景；Agent 不运行真实 mutation。

## 非目标

- 不定义或强制 `append_last`、`prepend_first` 等默认落点。
- 不增加 `after_*_id`、`page_level`、index 或通用 position 输入参数。
- 不在 Reparent/Copy/Move 内部隐式调用公开 Reorder，也不把 root-only Reparent 的“后代提升→目标根换父级”两阶段 mutation 描述为原子操作。
- 不新增、模拟或恢复 `reorder_section_group`。
- 不把 `destination_position` 放入 Copy fidelity 判定，或用位置观察替代 fresh identity、父级、内容、拓扑和删除安全验证。
- 不为 Plan 返回预测位置，不为递归子树返回完整位置清单。
- 不把 hierarchy child offset 扩展成跨类型混合排序合同。
- 不扫描已关闭 Notebook、用户文件系统或 OneNote UI 全局 Notebook 顺序。

## 完成定义

- 十个执行工具都以兼容的顶层 `destination_position` 返回目标根的最终观察位置；四个 Plan 工具不虚构最终位置。
- Page/Section/SectionGroup 成功响应为 `observed`，字段与同一次最终 read-back 一致；Notebook Copy 稳定返回 `not_applicable`。
- `reparent_page` 新增默认 `false` 的 `include_descendants`；所选 Page 可以原本处于任意合法 level，root-only 与 subtree 路线分别满足“保留后代整体提升”与“完整子树迁移”的身份、拓扑和内容合同，所选 Page 在 destination 中均成为根 Page。
- root-only 提升后 Reparent 失败、subtree 部分迁移或 mapping 不完整都有结构化 partial outcome、精确当前状态和失败保留证据，不自动补偿。
- Move 在源删除后重新计算位置，同父级 Page Move 不复用 Copy 阶段旧索引。
- 除 `reparent_page.include_descendants` 外，现有公开输入 schema 保持兼容；policy、Copy fidelity、Move 删除门和 raw XML 隐藏边界不变。
- 工具不承诺固定首位/末位，不为位置报告调用 Reorder 或任何额外 mutation。
- partial outcome 在能精确解析 target 时报告观察位置，否则返回结构化 `unavailable`，不猜测或自动补偿。
- Page 位置响应只覆盖 fresh 目标根在最终 Section 完整扁平 Page 序列中的 `index/sibling_count`，不返回 level、derived parent 或后代位置。
- 自动化位置矩阵覆盖十个执行工具；expected position 均由不调用生产 builder 的独立 snapshot helper 计算，并与成功/partial response 深比较，覆盖 Page fresh ID、容器两类父级、SectionGroup 固定排序、Notebook 不适用和 Move 最终观察时点。
- 十个既有 manual-validation 场景均保存独立 `destination-position-evidence*.json` 并逐字段核验返回值；字段不一致必须非零退出并保留现场。用户确认增强场景的真实证据后，才能认为相应工具的后端位置回传合同成立。
- 新 `reparent-page-with-level` 的 dry-run/静态权限/失败保留合同通过，用户确认 `root-only-default` 与 `full-subtree` 真实证据；两个 case 同样使用独立 position evidence，只描述 fresh 目标根。Agent 只运行 dry-run/纯测试。
- tool contracts、object model、architecture、README、manual-validation README、TODO 002/005/006/009/012/015/017 与 TODO 索引保持一致。

## 当前实施状态（2026-08-14）

代码、公开契约和纯验证已经交付：十个执行工具返回统一 `destination_position`，Move 在源删除后重新投影；`reparent_page` 已加入默认 `false` 的 `include_descendants`，并实现 root-only 后代提升与完整子树路线；自动化合同覆盖目标根位置、Notebook 不适用、fresh ID、分叉 Page 范围、同父级 Page Move 的删除后索引，以及 promotion/Reparent 各阶段的结构化 partial outcome。十个既有 manual-validation runtime 已接入独立 after-snapshot projector；所有 hierarchy-child destination fixture 均准备至少两个可区分的同类型 anchors；新的 `reparent-page-with-level` 已注册 Scenario-owned recipe、最小静态权限和正式 dry-run case。

纯验证记录：完整 pytest 的交付基线为 `845 passed`，后续共享 manual-validation 稳定性与 mutation 安全强化纳入后曾达到 `1037 passed`；清理重复 dry-run/orchestrator 展开、旧逐项 activation fake 路径和历史 tombstone并完成本轮注册后，当前等价行为基线为 `926 passed`，其中 manual-validation 纯合同收集 `542` 项。`reparent-page-with-level --dry-run --json` 返回 `ok=true`、`server_started=false`；`all --dry-run --json --verbosity quiet` 返回 `16 passed, 0 failed`。Agent 未启动任何真实 mutation；下述真实 run 均由用户本人在交互式前台终端启动，Agent 只读取保存的 evidence。

### 最新真实运行进度

用户在 2026-08-14 启动的当前版本 `all --use-cache` 完整批次产生 15 个 child run，最终为 `15 passed, 0 failed`。其中与本 TODO 的十个既有位置场景对应的证据为：`reparent-section`（`run-2026-08-14-11-19-40`）、`reparent-page`（`run-2026-08-14-11-20-38`）、`reparent-section-group`（`run-2026-08-14-11-21-06`）、`copy-page`（`run-2026-08-14-11-22-18`）、`copy-section`（`run-2026-08-14-11-24-58`）、`copy-section-group`（`run-2026-08-14-11-26-12`）、`copy-notebook`（`run-2026-08-14-11-27-34`）、`move-page`（`run-2026-08-14-11-28-18`）、`move-section`（`run-2026-08-14-11-29-20`）和 `move-section-group`（`run-2026-08-14-11-29-54`）。

十个 run 均为 `cache.decision=validated_hit`、`status=passed` 且 lifecycle 为 `closed_preserved`。保存的位置证据确认 Page、Section 与 SectionGroup 均为 `status=observed`，并覆盖 Reparent 的 7 个、Copy 的 10 个和 Move 的 4 个实际落点；Notebook Copy 按合同返回 `resource_type=notebook`、`status=not_applicable`。因此“手动位置验证矩阵”中的十个既有场景已经闭合，不再构成本 TODO 的阻塞项。

用户随后在前台完成 `reparent-page-with-level` 的三次真实运行：`run-2026-08-14-12-44-14`（fresh）、`run-2026-08-14-13-51-48`（validated cache hit）和 `run-2026-08-14-13-53-49`（fresh）均为 `status=passed`，lifecycle 均为 `closed_preserved`；`root-only-default` 与 `full-subtree` 的独立 `destination-position-evidence*.json` 均报告 `status=observed`。用户据此明确批准将场景注册到 `all`，其 `capability_assessment.validation_status` 同步收敛为 `passed`。

至此，代码、公开契约、纯测试、dry-run、十个既有位置场景和新范围双 case 的真实证据均满足完成定义，本 TODO 标记为“已完成”。
