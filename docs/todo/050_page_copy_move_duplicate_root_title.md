# 050：Page Copy/Move 目标同标题根 Page 回归

> ID：050
> 状态：已完成
> 优先级：P0
> 类型：Bug / Page Copy / Page Move / 精确身份
> 更新日期：2026-08-21

## 问题

当前 `copy_page` 与 `move_page` 在进入任何 OneNote 写入前，共用 `CopyService._destination()` 生成目标计划。该逻辑把目标 Section 下所有一级 Page 与目标标题做忽略大小写的比较；只要已有同标题 Page，就拒绝整个操作，并声称 Copy 不会覆盖、合并或自动改名。

这条门禁对 Section、SectionGroup 和 Notebook 的文件/目录式名称冲突有其必要性，但不适用于 Page。OneNote 可以在同一 Section 中拥有多个同标题 Page；当前 `MutationService.create_page()` 已取得 `CreateNewPage` 返回的 fresh allocated ID，并按 exact ID、Page 类型、目标 Section 和原始标题做回读验证。因而同标题 Page 不是覆盖风险，而是此前路径/名称式保守检查遗留的误拒绝。

`move_page` 是 Copy→验证→非永久删除源 Page 的重建式 Move，复用同一计划路径，所以也会在源删除前被同样拒绝。现有人工场景的同标题锚点覆盖的是复制子树中的子 Page；没有覆盖目标 Section 已含“与被复制/移动根 Page 同标题”的一级 Page，因此未发现本问题。

## 目标行为

1. `copy_page` 与 `move_page` 必须允许目标 Section 已存在一个或多个与目标根 Page 同标题（包括仅大小写不同）的一级 Page。
2. 每次 Page Copy 都必须通过 `CreateNewPage` 创建 fresh target；不得覆盖、合并、复用或按标题选择已有 Page。返回的 `id_map` 必须映射到本次 allocated/read-back target ID，而非同标题 anchor。
3. 创建回读继续以 allocated ID 为首选证据；仅当该 ID 不可见时，才允许“本次新出现、同目标 Section、同原始标题”的唯一候选作为 remap。存在多个候选时必须 fail closed。
4. `move_page` 仅在新 target 的内容、拓扑、fresh-ID 和 Copy fidelity 全部验证后，才可对 exact source ID 执行非永久删除；同标题 anchor 必须保持不变。
5. Section、SectionGroup 与 Notebook 的同名路径/文件系统冲突拒绝保持不变。不得以本修复放宽它们的命名规则，也不得引入自动改名。

## 实施范围

- 将 Page 与容器的 destination collision 规则分离：Page 目标父级及其 existing children 只进入 plan digest / 诊断快照，执行前不比较、也不构成漂移拒绝；仍不以同标题 Page 作为 Page Copy/Move 的计划拒绝条件；
- 保持 `destination_title` 的原始逻辑标题语义，包括大小写、Unicode 和已由 [TODO 040](040_move_readback_validation_followups.md) 覆盖的路径分隔字符；
- 审查 Page target 创建、`wait_for_created()` remap 和 Copy target validator，确保任何回退均不通过 display path 或标题从多个同名对象中任选一个；
- 更新公开 tool 描述、当前设计契约和用户文档，明确 Page 允许重名但 Copy/Move 按 exact ID 创建与验证；
- 不改变 Page 子树范围、目标排序、Copy fidelity tier、Copy budget、source drift、Move Copy-before-delete 或 non-permanent delete 的既有门限。

## 自动化合同

- Page Copy：目标 Section 预置一个与 source root 标题完全相同、以及仅大小写不同的一级 anchor；默认标题与显式 `destination_title` 均能创建 fresh Page，anchor 的 ID、内容、层级和顺序不变；
- Page Move：同样预置 root-title anchor，验证 target ID 不等于任何 before ID、所有 Copy gate 通过后才删除 exact source；anchor 不得被覆盖、重排或删除；
- 覆盖 root-only 和 `include_subpages=true`：完整子树中的 target IDs 都必须 fresh；任何同标题 anchor 均不得出现在 `id_map`；
- 负向注入：`CreateNewPage` 返回已有 anchor/source ID、allocated ID 与回读对象不匹配、allocated ID 缺席且有多个 fresh 同标题候选时，必须 fail closed，且 Move 不得删除源；
- Section、SectionGroup 和 Notebook 的同名目标继续在 mutation 前拒绝；现有容器冲突合同不回归；
- 现有“plan rejects case-insensitive direct name conflict”测试改为仅覆盖容器；新增 Page 目标同标题正向合同，防止再次把 Page 纳入通用名称拒绝。

## Human-gated 验收

在既有、自动创建的 disposable `copy-page` 与 `move-page` scenario 中加入与源根 Page 同标题的目标 Section anchor。每个 scenario 都必须记录 before/after 的 exact IDs、id map、内容/拓扑和 anchor 不变性；Move 还须证明删除发生在 verified Copy 之后且为 `permanently=false`。

真实 OneNote 执行只能由用户在交互式前台显式启动。pytest、CI、Agent、hook、import、timer、watcher 与 dry-run 不得触发真实 mutation；失败必须保留现场和证据。

## 进度

2026-08-21：生产实现、自动化合同、`copy-page` recipe v16、`move-page` recipe v8、公开契约与文档已落地。实现仅分离 Page 与容器的 collision 规则；跨 Create 类型的 failure-identity 加固已拆分到 [TODO 053](053_copy_create_identity_failure_evidence.md)，避免扩大本项影响面。用户在 2026-08-21 的两次 fresh `copy-page` run 都在 v15 fixture 阶段发现 casefold root-title anchor 被通用 `ensure_page()` 复用，因而在任何 Copy 前 fail closed；v16 改为显式创建并验证两个 fresh anchor ID。

用户随后明确确认 disposable `copy-page` 与 `move-page` 的同标题根 Page 手动验收通过；destination title 的 Copy→rename 两阶段修复与仅文字投影诊断也已纳入本项实现。完整 pytest 已通过 `1571 passed`，`copy-page --dry-run --json` 通过。真实验收结论来自用户确认，而非 Agent、pytest 或 dry-run；至此完成定义全部满足。

## 完成定义

- [x] Page Copy/Move 不再因目标 Section 的同标题一级 Page 被计划阶段误拒绝；
- [x] 新 target 的 allocated/read-back ID、exact Section、标题和 fresh identity 均被验证，且不复用同标题 anchor；
- [x] Copy/Move 的正向、歧义/别名负向和容器冲突保留测试通过；
- [x] 相关公开契约、README/用户文档、设计文档及 manual-validation 场景同步；
- [x] 聚焦测试与完整 pytest 通过；
- [x] 用户确认 disposable `copy-page` 与 `move-page` 的真实同标题根 Page 回归通过，且 Move 未误删 anchor 或源外对象。

## 关联

- [TODO 015](015_mutation_target_identity_hardening_and_duplicate_page_regression.md)：allocated-ID-first 创建验证及早期重名 Page 回归；本项补齐其未覆盖的“目标根 Page 直接同标题”入口拒绝。
- [TODO 040](040_move_readback_validation_followups.md)：Page 逻辑标题与 filesystem leaf 规则已分离；本项只处理同标题 target 的错误拒绝。
- [TODO 035](035_copy_move_internal_planning_and_agent_role.md)：Copy/Move 的单调用、服务端内部 planning 边界。
