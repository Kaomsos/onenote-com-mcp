# 017：Page Move 可选子树与跨 Notebook 双范围验收

> ID：017
> 状态：已完成
> 优先级：P1
> 类型：Mutation 安全 / Page Move 范围与真实后端验证
> 更新日期：2026-08-11

## 目标

让 `plan_move_page` / `move_page` 与 Page Copy 使用同一个 `include_descendants=false` 默认合同：省略或显式 `false` 只移动根 Page，显式 `true` 移动完整缩进子树。计划与执行范围不一致必须在 Copy 前拒绝。

root-only Move 不能直接删除仍拥有缩进后代的根 Page。实现必须先把被排除的完整后代子树整体提升一级，保持精确 ID、Section、相对层级和内容，回读通过后才允许对选定根 Page 调用 `DeleteHierarchy(permanently=false)`；删除后再次验证被排除后代仍活动。任一步不确定都 fail closed，并保存 Copy 目标和 source topology 的人工接管证据。

## 场景范围

具名 `move-page` 场景只覆盖跨 Notebook：

1. `cross-notebook-root-only`：省略参数，只复制/删除根 Page，被排除子页留在源 Notebook 并提升一级；
2. `cross-notebook-subtree`：显式 `include_descendants=true`，父子两页均复制并按叶到根非永久删除。

同 Notebook 跨 Section 不再在 Move 中重复，因为位置变化已由 typed `reparent-page` 场景覆盖。Move fixture 只使用最小、已验证的 Outline/RichText；逐内容类型保真由 Copy 场景及 comparator 负责，Move 只审计 `verified/lossless Copy → 精确范围 → 安全非永久删除` 组合。

## 自动化状态

- 公开 plan/execute schema、默认值、digest 范围绑定与 mismatch-before-copy 已覆盖；
- root-only 后代 plan binding、删除前提升、删除后 ID/Section/level/parent/content 保留已覆盖；
- subtree 继续覆盖叶到根删除、copy-only、源重校验、部分删除与回收站元数据不可见分支；
- recipe version 4 使用固定 `source`/`destination` 双 Notebook bundle 和两个独立源子树；
- manual-validation 纯测试与完整 pytest 已通过；Agent 未运行真实 scenario。

## 2026-08-11 root-only 真实失败与修复

用户运行 `move-page --use-cache` 的 `run-2026-08-11-20-19-29` 时，root-only Copy 已返回 `verified=true/lossless=true` 并创建目标，但子页提升后的内容门报告 `A preserved Move descendant changed content during promotion.`。证据同时证明：

- 提升用 `UpdateHierarchy` 已成功，源删除没有被调用；
- `source_deleted=false`，目标 Copy 与失败 working bundle 均保留；
- cache template inventory 保持 `all_templates_unchanged=true`。

根因是保留子页在计划中记录了完整 Page XML 的 raw SHA-256，而提升会按预期改变 Page 根 `pageLevel` 及 OneNote 管理的修改时钟；把 raw hash 用作提升前后正文比较必然产生假阳性。修复保持 plan 的 raw XML hash 用于提升前的 stale-plan 检查，但在预期拓扑 mutation 后改用 `stable_page_content_digest` 比较正文，并继续独立严格验证 Page ID、Section、顺序、level 和 parent。回归测试覆盖“只改变 `pageLevel`/时钟可通过”和“真实正文变化仍产生不同摘要”。

## 2026-08-11 用户真实复验

用户运行 `move-page --use-cache` 的 `run-2026-08-11-20-29-19`，两个固定 case 均通过：

- `cross-notebook-root-only`：`copy_verified=true`、有效范围为 `include_descendants=false`，只删除选定根 Page；被排除子页保持原 ID、仍在源 Section 活动，并通过提升后的拓扑与稳定内容验证；
- `cross-notebook-subtree`：`copy_verified=true`、有效范围为 `include_descendants=true`，父子两个源 Page 均非永久删除并退出活动树；
- 两个 case 均返回 `source_deleted_nonpermanently=true`，三个新目标 ID 只属于 destination role；COM 未提供 Page 回收站元数据时按既定合同记录 `not_required_com_unavailable`，不影响活动态缺席门；
- source/destination working Notebook 最终均精确关闭，`filesystem_deleted=false`，immutable cache template 未被打开。

该复验同时关闭了此前 raw XML hash 假阳性的回归。代码、自动化合同、双 case 真实证据和生命周期证据均满足完成定义，本 TODO 正式标记为“已完成”；结论仍仅限已记录环境和已验证的最小内容类型。

## 用户真实验收

先检查无副作用计划：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py move-page --use-cache --dry-run --json
```

确认输出包含两个 Notebook roles、两个固定 case、默认永久删除关闭后，只能由用户本人运行：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py move-page --use-cache --keep-worksite
```

本次用户运行已在 OneNote UI 与 evidence 中确认：

- root-only 目标只有一页；原子页仍在 source Notebook，提升为根页且正文不变；
- subtree 目标有父子两页；对应源父子页均不再出现在活动树；
- 三个目标只存在于 destination Notebook；
- 两个 case 的 `copy_report.verified/lossless=true`，删除调用只覆盖各自 `id_map` 源 ID，且均为 `permanently=false`；
- `--keep-worksite` 保留双 Notebook、三个目标和完整人工清理说明。

失败时立即停止并保留现场，不运行第二次真实命令来覆盖证据。

## 完成定义

- [x] 生产 plan/execute 契约支持默认 root-only 和显式 subtree，并绑定 `plan_digest`；
- [x] root-only Move 在删除前后保护被排除后代，失败语义结构化且 fail closed；
- [x] 场景只覆盖跨 Notebook root-only/subtree，不重复 Reparent 或内容类型取证；
- [x] 聚焦测试、manual-validation 纯测试、完整 pytest、dry-run 与 diff check 通过；
- [x] 用户运行并确认 recipe version 4 的双 case 真实 OneNote 证据（`run-2026-08-11-20-29-19`）。

## 关联

- [TODO 002](002_p2_copy_and_reconstructive_page_move.md)：原始 Page Move 重建与非永久删除合同；
- [TODO 005](005_page_copy_without_indentation_subtree.md)：Page Copy 的同名范围参数与历史证据；
- [`tool_contracts.md`](../design/tool_contracts.md)：当前公开参数、返回和失败合同；
- [`manual_validation/README.md`](../../tests/manual_validation/README.md)：用户真实运行和 evidence 说明。
