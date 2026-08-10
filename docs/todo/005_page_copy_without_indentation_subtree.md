# 005：Page Copy 可选排除缩进子树

> ID：005
> 状态：待办
> 优先级：P2
> 类型：公开工具契约 / Copy 语义
> 更新日期：2026-08-10

## 背景

当前 `plan_copy`/`copy_page` 把 Page Copy 固定定义为复制指定 Page 及其完整缩进子树。Page 的缩进后代是同一 Section 中具有独立 ID 的 Page，不嵌在根 Page 正文中，因此底层重建 Copy 可以只选择根 Page；当前缺口是公开参数、计划摘要、验证和人工场景尚未表达这种选择。

## 目标

为 Page Copy 增加向后兼容的范围参数：

```json
{
  "include_descendants": true
}
```

- `true` 保持现有默认语义：复制完整缩进子树；
- `false` 只复制 `page_id` 指定的 Page，不创建其缩进后代；
- 该参数同时进入 `plan_copy` 与 `copy_page`，并纳入 `plan_digest`；计划值和执行值不一致时必须在 mutation 前拒绝；
- 非 Page 源不得用该参数改变 Section、SectionGroup 或 Notebook 的递归 Copy 语义。

## 语义与安全边界

- 单页副本在目标 Section 中创建为 `page_level=1`，保持当前 Page Copy 的目标根语义；
- `id_map`、`created_ids`、预算估算、`copied_counts` 和 `page_results` 只包含实际选择的 Page；
- 指向被排除后代的链接视为复制范围外链接，保留原目标，不改写为不存在的新 ID；
- 源 Page、被排除的后代及其层级关系均不得被修改；
- 名称冲突、内容保真 tier、未知节点、部分失败和不覆盖/不合并规则保持不变；
- `plan_move_page`/`move_page` 继续处理完整 Page 子树，本 TODO 不开放仅移动根 Page 的 Move；
- Section、SectionGroup 和 Notebook Copy 继续递归复制全部后代。

## 实施范围

1. 扩展 Page Copy 的 tool/service 参数、源快照选择和稳定 `plan_digest` payload；
2. 让计划的 `estimated`、`snapshots.source`、步骤计数和 Copy budget 只反映实际选择范围；
3. 调整链接改写输入集合，使未选择的缩进后代保持范围外链接语义；
4. 为默认完整子树与显式单页两种模式补充成功、过期计划、参数不一致、预算、名称冲突、保真和部分失败合同测试；
5. 在 `tests/manual_validation/` 增加具名、隔离、human-gated 的单页 Copy 场景，使用 Parent/Child fixture 证明只创建 Parent 副本、Child 保持源端且没有目标映射；真实执行仍只允许用户显式启动；
6. 实现完成后同步更新 `docs/design/tool_contracts.md`、根 README、对象—操作矩阵和人工验证文档。

## 完成定义

- `include_descendants` 默认值保持现有完整子树行为，现有调用者无需修改；
- `include_descendants=false` 时只创建并验证指定 Page，源缩进后代不出现在 `id_map` 或目标 hierarchy；
- 参数已绑定进计划摘要，计划/执行范围不一致会在任何 mutation 前 fail closed；
- 自动化合同覆盖默认兼容性、单页范围、链接、预算、策略拒绝和部分失败；
- 具名 manual-validation scenario、dry-run、权限/tool allowlist 和证据字段齐全；
- 用户在 disposable Notebook 中显式确认真实 OneNote 单页 Copy 行为后，记录 OneNote/Office 版本与证据；
- 当前设计文档、README、人工验证文档和 TODO 索引同步更新。
