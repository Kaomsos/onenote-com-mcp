# 005：Page Copy 默认仅复制单页，可选包含缩进子树

> ID：005
> 状态：已完成
> 优先级：P2
> 类型：公开工具契约 / Copy 语义
> 更新日期：2026-08-11

## 背景

当前 `plan_copy`/`copy_page` 把 Page Copy 固定定义为复制指定 Page 及其完整缩进子树。Page 的缩进后代是同一 Section 中具有独立 ID 的 Page，不嵌在根 Page 正文中，因此底层重建 Copy 可以只选择根 Page；当前缺口是公开参数、计划摘要、验证和人工场景尚未表达这种选择。

## 目标

为 Page Copy 增加范围参数，并将默认行为改为只复制指定 Page：

```json
{
  "include_descendants": false
}
```

- 省略参数或显式传入 `false` 时，只复制 `page_id` 指定的 Page，不创建其缩进后代；
- 只有显式传入 `true` 时，才复制指定 Page 及其完整缩进子树；
- 该默认值变更会改变未显式传参的既有调用语义，实施时必须在 tool 描述、README 和迁移说明中明确记录；
- 该参数同时进入 `plan_copy` 与 `copy_page`，并纳入 `plan_digest`；计划值和执行值不一致时必须在 mutation 前拒绝；
- 非 Page 源不得用该参数改变 Section、SectionGroup 或 Notebook 的递归 Copy 语义。

## 语义与安全边界

- 单页副本在目标 Section 中创建为 `page_level=1`，保持当前 Page Copy 的目标根语义；
- `id_map`、`created_ids`、预算估算、`copied_counts` 和 `page_results` 只包含实际选择的 Page；
- 指向被排除后代的链接视为复制范围外链接，保留原目标，不改写为不存在的新 ID；
- 源 Page、被排除的后代及其层级关系均不得被修改；
- 名称冲突、内容保真 tier、未知节点、部分失败和不覆盖/不合并规则保持不变；
- 本 TODO 当时只调整 Page Copy；`plan_move_page`/`move_page` 的范围参数由后续 2026-08-11 变更独立实现，不计入本 TODO 原始完成门；
- Section、SectionGroup 和 Notebook Copy 继续递归复制全部后代。

## 实施范围

1. 扩展 Page Copy 的 tool/service 参数、源快照选择和稳定 `plan_digest` payload；
2. 让计划的 `estimated`、`snapshots.source`、步骤计数和 Copy budget 只反映实际选择范围；
3. 调整链接改写输入集合，使未选择的缩进后代保持范围外链接语义；
4. 为默认单页与显式完整子树两种模式补充成功、过期计划、参数不一致、预算、名称冲突、保真和部分失败合同测试；
5. 在 `tests/manual_validation/` 增加具名、隔离、human-gated 的单页 Copy 场景，使用 Parent/Child fixture 证明只创建 Parent 副本、Child 保持源端且没有目标映射；真实执行仍只允许用户显式启动；
6. 实现完成后同步更新 `docs/design/tool_contracts.md`、根 README、对象—操作矩阵和人工验证文档。

## 完成定义

- `include_descendants` 默认值为 `false`，省略参数时只创建并验证指定 Page，源缩进后代不出现在 `id_map` 或目标 hierarchy；
- `include_descendants=true` 时复制并验证完整缩进子树，保持原有完整子树能力；
- 默认语义变化已在 tool 描述、README 和迁移说明中明确告知调用方；
- 参数已绑定进计划摘要，计划/执行范围不一致会在任何 mutation 前 fail closed；
- 自动化合同覆盖默认单页语义、显式完整子树、链接、预算、策略拒绝和部分失败；
- 具名 manual-validation scenario、dry-run、权限/tool allowlist 和证据字段齐全；
- 用户在 disposable Notebook 中显式确认真实 OneNote 单页 Copy 行为后，记录 OneNote/Office 版本与证据；
- 当前设计文档、README、人工验证文档和 TODO 索引同步更新。

## 实施与验证记录

- 2026-08-10：`plan_copy` 与 `copy_page` 已增加默认 `false` 的 `include_descendants`。Page source 默认 snapshot/估算/预算/步骤只包含根 Page；显式 `true` 保留完整缩进子树。Section、SectionGroup、Notebook Copy 继续递归；当时 Page Move 仍强制完整子树。
- 2026-08-10：摘要 schema 已绑定有效范围值。计划与执行值不一致会在任何目标创建前返回 stale-plan 错误；`id_map`、`created_ids`、计数、Page 结果和链接改写输入只覆盖已选择 Page，指向排除后代的链接保留原目标。
- 2026-08-11：具名 human-gated `copy-page` 场景扩展为一次运行覆盖两种情况。`root-only-default` 在 plan/execute 中均省略 `include_descendants`，独立断言只有 Parent 进入 plan/id_map、目标只新增一个 level-1 Page、Child 在源 Section 的 ID/父级/level/order/内容 hash 保持不变；`full-subtree` 显式提交 `include_descendants=true`，独立断言 Parent/Child 均被映射，且目标父子关系、相对层级、顺序和内容语义保持。两个 case 分别稳定 plan、绑定 before、保存 mutation response 和 after；默认逆序清理两个目标，`--keep-worksite` 同时保留两种目标。
- 2026-08-11：fixture 新增 `00-Description/00-Copy-Page-Description`，在 OneNote UI 内直接说明原始 `Source/01-Source-Parent/02-Source-Child` 拓扑、默认仅根页目标、显式完整子树目标和默认清理后的状态；创建阶段会回读说明页并检查关键状态标记。聚焦 manual-validation 纯合同已通过；真实 OneNote mutation 未由智能体执行。
- 2026-08-11 后续扩展：Page Move 现已采用同名、同默认值的 `include_descendants` 合同。root-only Move 在删除根页前绑定并提升被排除后代，显式 `true` 才移动完整子树；具名场景只覆盖跨 Notebook 两种范围。用户已在 `run-2026-08-11-20-29-19` 独立确认更新后的 Move 双 case 真实验收通过；该结果不改变本 TODO 的 Page Copy 证据边界。
- 2026-08-11：人工验收使用 `.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --keep-worksite`，先打开 Description Page，再在 Destination 对照 `01-Root-Only-Copy-*`（无子页）与 `02-Full-Subtree-Copy-*`（含 level-2 Child），并确认 Source Parent/Child 均保持原层级。
- 2026-08-11：用户明确确认 TODO 005 已通过人工验收。真实运行证据位于 `.local-validation/run-20260810T160750Z/`：scenario/result 与 run-state 均为 `passed`，`worksite_preserved=true`；`root-only-default` 映射 1 个 Page，`full-subtree` 映射 2 个 Page，两个 Copy report 均为 `verified=true`、`lossless=true`。验收环境为 local-onenote-mcp `0.1.0`，本机 OneNote Desktop `16.0.20228.20158`（`Office16/ONENOTE.EXE`）。据此完成定义中的真实 OneNote 证据门已满足，本 TODO 标记为“已完成”。
