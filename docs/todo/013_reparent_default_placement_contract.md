# 013：Reparent 默认落点与 Agent 可见顺序合同

> ID：013
> 状态：待办
> 优先级：P2
> 类型：公开 mutation 契约 / 默认顺序与 Tool 描述
> 更新日期：2026-08-10

## 背景

当前 `reparent_page`、`reparent_section` 和 `reparent_section_group` 只明确对象会换到哪个父级，没有在公开代码合同、设计文档、tool 描述或成功响应中说明它会落在目标容器的什么位置。Agent 因而无法可靠判断目标对象会成为首项、末项，还是由 OneNote 后端决定。

此前考虑给 Page 增加 `after_page_id` / `page_level`、给 Section 增加 `after_section_id`，把 Reparent 与 Reorder 组合进一次调用。该接口不对称：Page 还涉及 level 和 ID remap，Section 只涉及 predecessor，SectionGroup 又没有可用的 Reorder；同时会引入双 policy、两次 mutation 和复杂的 partial-failure 合同。

本 TODO 改为先定义 Reparent 自身的默认落点，并直接告诉 Agent。首选合同是 `append_last`：Reparent 只负责换父级并追加到目标同类型序列末尾；需要自定义位置时，再显式调用独立的 `reorder_page` 或 `reorder_section`。不在 Reparent schema 中新增位置参数，也不隐藏第二次 Reorder mutation。

## 当前证据边界

- 当前 typed hierarchy XML 把目标对象作为目标父级 fragment 的最后一个 child，但 fragment 不包含目标父级的完整 sibling 序列；代码形状表达追加意图，不能单独证明 OneNote 的最终排序语义。
- 用户运行的 `reparent-page` 证据显示：目标 Section 原有一个 Page anchor，Reparent 后 anchor 保持第 0 位，移动后的 Page 位于第 1 位且 `page_level=1`。这支持当前环境中的 Page `append_last`，但还没有多个 anchors 的合同断言。
- 用户运行的 `reparent-section` 证据中，Group→Notebook 路线把 Section 放在已有 Description Section 之后；其他目标多为空，尚不足以证明 Notebook 与 SectionGroup 两类父级、多个 Section siblings 下都稳定追加。
- 现有 manual-validation 场景验证了换父级、身份、内容和拓扑，没有把目标末位作为 invariant。
- `reparent_section_group` 的顺序受当前后端固定名称排序约束，不能把 XML child 顺序解释为调用方可控位置。

因此，`append_last` 是要通过实现验证和真实场景补证的目标合同，不是已由现有稀疏证据证明的产品事实。

## 目标合同

### `reparent_page`

- 将 Page 追加为目标 Section 的最后一个根 Page；最终 `page_level=1`。
- 目标 Section 中既有 Page 的相对顺序、level 和派生 `parent_page_id` 不变。
- 继续只接受没有 parent Page、也没有 child Page 的根 Page 来源。
- OneNote 仍可能重映射 Page 和内容对象 ID；调用方必须使用返回的 fresh `item.id` / `id_map`。
- 如需指定 predecessor 或最终 level，Agent 在 Reparent 成功后使用 fresh Page ID 调用独立的 `reorder_page`。

### `reparent_section`

- 将 Section 追加为目标 Notebook 或 SectionGroup 的最后一个直属 Section sibling。
- 目标父级中既有 Section 的相对顺序和不相关层级关系不变。
- Section 自身和全部后代 ID、Page 内容及 Page 内部顺序/缩进关系保持。
- 如需指定 predecessor，Agent 在 Reparent 成功后调用独立的 `reorder_section`；该调用继续受独立的 Section Reorder policy 控制。

### `reparent_section_group`

- 本 TODO 不承诺 `append_last`，也不提供自定义位置。
- Tool 描述明确说明最终 SectionGroup 顺序遵循 OneNote 当前的固定名称排序，调用方不能通过 Reparent 控制位置。
- 不新增、模拟或隐式调用 `reorder_section_group`。

## Agent 可见的 Tool 描述

公开 tool 定义本身至少包含：

- `reparent_page`：同 Notebook 换到目标 Section，并追加为最后一个根 Page（level 1）；操作可能重映射 ID；如需自定义顺序或缩进，使用响应中的 fresh ID 再调用 `reorder_page`。
- `reparent_section`：同 Notebook 换到目标 Notebook/SectionGroup，并追加为最后一个直属 Section；如需自定义位置，再调用 `reorder_section`。
- `reparent_section_group`：同 Notebook 换父级；最终顺序由固定名称排序决定，不支持调用方指定位置。

描述不得暗示 Reparent 与后续 Reorder 是原子操作。Agent 应能判断第二次调用是新的 mutation，需要 fresh confirmation，并可能要求独立 policy 门。

## 成功响应

Page 和 Section 的成功响应增加可机器判断的 placement 信息：

```json
{
  "placement": {
    "strategy": "append_last",
    "sibling_type": "page",
    "predecessor_id": "fresh-or-existing-id",
    "final_index": 3,
    "page_level": 1,
    "verified": true
  }
}
```

- Section 使用 `sibling_type="section"`，`page_level` 为 `null` 或省略。
- 目标此前没有同类型 sibling 时，`predecessor_id=null`、`final_index=0`。
- `verified=true` 只能在 read-back 已证明目标为末位、既有 siblings 相对顺序未变后返回。
- SectionGroup 返回不同策略，例如 `backend_fixed_name_order`，不得复用 `append_last`。
- 保留现有 `item`、parent IDs、`id_map`、`verified` 和 `warnings` 契约。

## 实现方案与决策门

| 方案 | 优点 | 风险/限制 | 结论 |
| --- | --- | --- | --- |
| 保留当前 minimal typed XML，并在 mutation 后验证末位 | 改动最小；符合现有证据 | 后端若忽略 child 顺序，会在 mutation 后才发现 | 首选，先以真实场景证明 |
| 构造包含完整 sibling 序列的 ancestor-complete XML | 能更明确表达最终序列 | 更新面扩大，可能改写无关 siblings，必须另行探针证明 | 首选失败时再评估 |
| Reparent 后内部隐式调用 Reorder | 可强制位置 | 双 policy、非原子和 Page/Section 不对称 | 拒绝 |
| 声明 `backend_determined` 并回报观察位置 | 最保守 | Agent 不能依赖统一默认位置 | `append_last` 不稳定时的安全回退 |

XML 形状本身不是成功条件，最终合同以回读 invariants 和用户运行的真实场景为准。若 Page 或 Section 的合法路线无法稳定证明追加语义，必须回退到 `backend_determined`，同步 tool 描述和响应，不能通过隐藏 Reorder 制造一致表象。

## 建议代码重构

1. 增加内部 placement contract/枚举，至少区分 `append_last` 与 `backend_fixed_name_order`；公共 schema 不增加位置参数。
2. `_reparent()` mutation 前捕获目标父级的有界、同类型 sibling 序列和既有拓扑；Page 使用显式 `order`，Section 使用 hierarchy traversal 中稳定定义的直属 Section 序列。
3. mutation 后复用 fresh-ID 解析与 read-back，在有界 retry 内验证目标为同类型末项、既有 siblings 相对顺序和无关拓扑未变。
4. Page 额外验证 `page_level=1`，并在 ID remap 后用 fresh ID 计算 predecessor 和 final index。
5. 将结果写入统一的 placement response builder；SectionGroup 使用独立策略。
6. typed XML helper 的命名和注释可以表达追加意图，但仍以回读为事实来源；生产层继续不能接收 raw XML。
7. Tool adapter 只补描述和响应类型，不注册内部 XML、position planner 或通用 hierarchy mutation 入口。

## 失败与不确定状态

Reparent 可能已经改变父级，随后末位验证失败。此时不能返回暗示零 mutation 的普通错误，也不能自动 Reorder 或 Reparent 回滚。应返回结构化 partial outcome：

```json
{
  "partial": true,
  "outcome": "reparented_placement_unverified",
  "item": {"id": "fresh-current-id"},
  "destination_parent_id": "...",
  "expected_placement": {"strategy": "append_last"},
  "observed_placement": {"final_index": 1, "is_last": false, "page_level": 1},
  "manual_recovery_required": true
}
```

响应尽可能包含 fresh ID、当前父级、观察到的 sibling 序列摘要和 ID mapping。不得自动重试 mutation；只允许预算内 read-back retry。若 mutation 前无法有界读取目标序列，应 fail closed、零 mutation。

## Manual-validation 方案

扩展现有 `reparent-page` 与 `reparent-section` 场景，不新增需要 Reorder 权限的组合场景。

### Page

- 目标 Section 至少准备三个编号根 Page anchors。
- 来源 Page 初始位于源 Section 末尾，使反向 Reparent 的同一追加合同能恢复 before 顺序。
- 正向验证 fresh Page 位于所有 anchors 之后、level 1，既有 Page 顺序/level/parent 不变。
- 反向恢复继续核对追加位置、内容对象 ID mapping、富内容语义和 before/after 等价。

### Section

- Notebook 与 SectionGroup 两类目标父级都准备多个编号 Section anchors。
- 三条既有合法路线都覆盖非空目标序列，验证目标 Section 追加在最后、anchors 相对顺序不变。
- 来源 Section 初始位于源父级 Section 序列末尾，使反向 Reparent 可以恢复顺序。
- 继续验证 Section/后代 ID、Page 内容、Page order/level/parent 和无关对象不变。

旧证据需要由用户在场景增强后重新运行，因为旧 fixture 多数目标为空且没有默认落点断言。真实 mutation 仍只能由用户显式启动；Agent 只可运行 `--dry-run` 和纯测试。

## 自动化合同要求

- 三个 reparent tool schema 不增加位置参数；现有参数保持兼容。
- tool 描述覆盖默认末位、Page level 1、fresh ID、显式后续 Reorder，以及 SectionGroup 不可控排序。
- mutation 前 snapshot 有界；预算、类型、父级或顺序不可确定时零 mutation。
- Page 覆盖空目标、多个 Pages、ID remap、最终 level 1、既有 order/level/derived parent 不变。
- Section 覆盖 Notebook/SectionGroup 目标、多个 Sections、末位和既有相对顺序不变。
- 覆盖后端忽略末位、并发 sibling 变化、COM 错误和 read-back 超时的 failure/partial-failure。
- 成功响应的 strategy、predecessor、index、level 和 verified 与回读一致。
- Reparent 路径不调用任何 Reorder 或通用/raw hierarchy mutation helper。
- 生产注册表、错误和响应不暴露 hierarchy XML；manual validation 保持静态最小权限与失败证据保留。

## 风险与缓解

### P1：后端不保证末位

父级可能已经改变，位置却不符合合同。保存 mutation 前序列并强制回读；失败返回 fresh partial state，不隐藏 Reorder。真实场景不稳定时回退 `backend_determined`。

### P1：Section 没有显式 order 字段

在 parser 中定义并测试直属 Section 的稳定遍历序列，以 UI 编号和保存证据交叉核对两种父级；无法证明时不承诺 append。

### P1：Page ID remap 后引用旧 ID

完成一对一 mapping 后只使用 fresh Page ID 计算最终 placement，响应保留 `id_map`。

### P2：反向追加不能恢复任意原位置

场景把目标预置为源容器末项。产品文档说明通用调用若需恢复原位置，必须保存 anchor 并另行显式 Reorder。

### P2：Agent 把两次调用误认为原子

Tool 描述明确后续 Reorder 是独立 mutation；Reparent 响应提供 fresh ID 和已验证默认位置。

## 依赖与实施顺序

1. 以 [TODO 009](009_typed_reparent_tools_and_hide_raw_hierarchy_xml.md) 的 typed Reparent、ID mapping 和 raw XML 隐藏边界为基础。
2. 复用 [TODO 006](006_typed_section_and_section_group_reorder.md) 的独立 Reorder 合同作为自定义位置的显式后续操作；继续接受 SectionGroup Reorder 不支持的结论。
3. 先补 sibling sequence 的 parser/service 合同和多 anchors mock，再扩展现有 manual-validation fixtures/assertions。
4. 用户重新运行增强场景，分别确认 Page、Section→Notebook、Section→SectionGroup 的追加语义。
5. 证据成立后更新 tool 描述、object model、architecture、README 和场景文档；否则采用 `backend_determined` 回退合同。
6. TODO 010/011 完成后，把增强场景的 dry-run 和 fixture recipe 接入相应框架，但它们不是定义默认落点的前置条件。

## 非目标

- 不增加 `after_*_id`、`page_level` 或通用 position 参数。
- 不组合 Reparent 与 Reorder，不引入双 policy 或隐藏第二次 mutation。
- 不支持或模拟 SectionGroup Reorder。
- 不扩展跨 Notebook Move、child Page 子树、名称/path 定位或 raw XML 输入。
- 不仅凭当前 XML child 顺序或稀疏历史证据宣称 `append_last` 已通过。
- 不承诺两个独立 tool 调用之间的原子性或自动补偿。

## 完成定义

- 代码中存在明确的 placement contract；Page/Section 为经验证的 `append_last`，SectionGroup 为固定后端顺序。
- Page/Section tool 描述直接告诉 Agent 默认末位、Page level 1、fresh ID 以及自定义位置应调用哪个工具。
- schema 不增加不对称位置参数，生产路径不隐式调用 Reorder。
- 成功响应包含与回读一致的结构化 placement；空和非空目标的 predecessor/index 有合同测试。
- Page 多 anchors、Section 两类父级多 anchors、既有相对顺序和无关拓扑有自动化覆盖。
- placement 验证失败返回 fresh `reparented_placement_unverified`，不自动回滚或 Reorder。
- 增强场景的 dry-run 合同和静态权限通过；Agent 未执行真实 mutation。
- 用户分别运行并确认增强后的真实场景，保存 UI 顺序、before/after 和机器证据。
- 若任一合法路线无法稳定证明 `append_last`，完成前改用 `backend_determined`，同步描述、响应、测试和文档。
- tool contracts、object model、architecture、README、manual-validation README、TODO 006/009/010/011 和 TODO 索引一致。
