# 053：Copy Create identity failure 证据收敛

> ID：053
> 状态：进行中
> 优先级：P2
> 类型：Reliability / Copy Create / Failure Evidence
> 更新日期：2026-08-21

## 问题

TODO 050 只修复 Page Copy/Move 的同标题目标拒绝。对 Notebook、Section、SectionGroup 与 Page 的 Create 回读做跨类型 identity hardening，属于独立的 failure-evidence 契约，不能借同标题修复一并改变所有 Copy 的失败 envelope。

特别是“Create 返回 allocated ID、但后续 read-back 未确认”的状态不能仅因 `created_ids` 尚为空就标记为 `rejected_preexisting_ids`；它可能是已创建但尚未收敛的未知状态，必须保留准确的 recovery evidence。

## 范围

- 审查四类 Create 的 allocation、read-back、remap 与 PartialFailure 证据；
- 仅在 mutation 边界能够证明时输出 `rejected_preexisting_ids`，其余未确认 allocation 进入 `possibly_untracked_allocated_ids`；
- 如需标识 backend 已尝试/已接受 Create，由 MutationService 给出明确、类型化的证据，CopyService 不得猜测 COM 已执行；
- 保持 Page allocated-ID-first 和 unique fresh remap 的现有行为；不按 display path 或同名对象选择 mutation target；
- 为成功、明确已存在、read-back 不确定和 backend 后异常分别补充确定性合同测试，并在需要 mutation-policy 的真实路径保留具名 manual-validation scenario。

## 非范围

- 不修改 Page 同标题允许规则；该行为由 [TODO 050](050_page_copy_move_duplicate_root_title.md) 管理；
- 不放宽 Section、SectionGroup 或 Notebook 的同名路径/文件系统冲突拒绝；
- 不触发真实 OneNote mutation。真实验收只能由用户在交互式前台显式执行。

## 进度

2026-08-21：已完成与 [TODO 050](050_page_copy_move_duplicate_root_title.md) 的范围切分，并冻结本项的 failure-evidence 缺口：Page 的同标题目标规则只影响成功 Create 后的目标选择，不能借此改变四类 Create 在 allocation/read-back 未确认时的 PartialFailure 语义。本次仅建立跟踪与证据边界，不修改跨类型 Create 的 failure envelope，也不把自动化或 dry-run 记作真实 OneNote 验收。

下一步仍是逐类审查 MutationService 的 Create outcome 与 CopyService 的收敛失败路径，先确定可证明的 backend-attempt/allocated-ID 证据，再补纯合同和具名人工验证 scenario。完成定义中的所有验证项仍未满足。

## 完成定义

- [ ] 四类 Create 的成功与失败 identity evidence 具有明确、互斥且 fail-closed 的语义；
- [ ] 不会把未知 allocation 错误标记为已存在对象，也不会遗漏可能需要人工恢复的 ID；
- [ ] 自动化合同和对应具名 manual-validation scenario 通过；
- [ ] 用户确认需要的 disposable 真实 OneNote 验收通过。

## 关联

- [TODO 015](015_mutation_target_identity_hardening_and_duplicate_page_regression.md)：既有 allocated-ID-first 创建验证；
- [TODO 050](050_page_copy_move_duplicate_root_title.md)：Page Copy/Move 的同标题目标回归。
