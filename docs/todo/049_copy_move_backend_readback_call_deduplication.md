# 049：Copy/Move backend readback 调用去重

> ID：049
> 状态：待办
> 优先级：P2
> 类型：性能 / Copy / Move / strict readback / backend call
> 更新日期：2026-08-19

## 决策摘要

TODO 045 已交付调用专属、按 mutation epoch 失效的 `CopyReadCache`，消除了 CopyService 自身同一只读阶段的重复读取。本项只处理仍由共享 `MutationService` 和 hierarchy XML 构造辅助路径绕开该 snapshot 所产生的重复 backend call。

优化不得降低 strict Copy fidelity、创建/删除收敛、Move 的 Copy-before-delete、source drift 检查或非永久删除的 typed outcome 证据。目标是让已由同一 epoch 的完整 hierarchy snapshot 证明的信息以类型化内部参数传递，而不是再次调用 OneNote；每次状态改变后仍必须重新获取 live evidence。

这与 [TODO 048](048_pywin32_persistent_com_bridge.md) 相互独立：048 降低每次 backend call 的 transport 固定成本；本项减少需要发出的 backend call 数量。

## 已观察基线（非验收证据）

2026-08-19 的本地 content-free Debug Trace 中，严格单页 `move_page` 成功路径稳定为 25 个 backend call，其中 16 次 `get_hierarchy`、4 次 `get_page_content`、5 次写入类调用。多页路径符合约 `18 × Page 数 + 7` 的线性关系：2/3/5 页分别为 43/61/97 次 backend call。

Read reason 将可合并的层级读取定位为：

- 每个创建对象有两次 `destination_precondition`；共享创建服务分别读取 parent/resource 和完整 hierarchy；
- 每个待删 Page 有两次 `delete_confirmation`；Move 已有 source-drift snapshot，但 `delete_page()` 和其内部 `delete_resource()` 又各自读取；
- Page 排序前的 `topology_verification` 中，`page_order_xml()` 为构造 ancestor XML 再读取一次 hierarchy，尽管 caller 已持有同 epoch catalog；
- 单页删除后有五次 `delete_convergence` hierarchy read，其中 reconciliation observation、稳定收敛、Page scope final check 与最终 destination position 存在可验证的 observation/snapshot 复用机会。

上述数字只作为实现前的本地样本，不构成对所有 OneNote 数据形态或端到端耗时的承诺。Debug Trace 的相邻 dispatch 间隔同时包含 COM、bridge transport、Python 处理与收敛等待，不能单独解释为 COM latency。

## 工作范围

### A. 创建前置 snapshot 传递

1. 为 `create_page`、`create_section`、`create_section_group` 与 `create_notebook` 设计仅内部使用的、类型化且 epoch 校验的 hierarchy preflight 参数。
2. 在无 mutation 的同一 epoch 内，令 CopyService 已持有的 snapshot 同时满足 parent/resource 校验和 `before_ids` 分配安全检查；不得传递可变 cache 本体。
3. 若 preflight 缺失或 epoch 不匹配，必须 fail-safe 回退到一次新的完整 hierarchy read；不得从旧 snapshot 推断新对象或可删除对象。
4. 创建后的 `wait_for_created` 双稳定观察仍保持 live read，不以创建前 snapshot 替代。

### B. Move 删除前置与删除后 observation 复用

1. 在 source drift 检查后，将当前 epoch 的 typed hierarchy snapshot 传入 `delete_page` / `delete_resource`；删除前确认、Page scope 和 descendant 选择只能消费同一 epoch 的显式 snapshot。
2. 对 root-only Page descendant promotion：任何 `update_hierarchy` 后必须丢弃旧 preflight，重新取得 live snapshot，再继续删除。
3. 让删除 reconciliation 的成功观察作为 `_converge(..., initial_value=...)` 的首样本，同时仍要求既有数量的稳定 observation；partial、timeout、COM error 与 recycle-bin metadata 不可见时保持原有 fail-closed 语义。
4. 若同一 post-delete `HierarchySnapshot` 已满足 Page scope final check 和最终 destination-position 投影，可在内部复用；不得把 snapshot 暴露到 tool response、audit 或 debug trace。

### C. 排序 XML 的 catalog 注入

1. 为 `HierarchyService.page_order_xml()` 增加可选的内部 catalog 参数，复用 caller 已验证的 topology snapshot，行为与已有 `container_order_xml(..., catalog=...)` 对齐。
2. 未提供 catalog 时保持现有独立读取和结果合同；提供的 catalog 必须完整覆盖构造 ancestor chain，否则 fail closed。
3. 排序 mutation 后的 topology convergence 必须继续 fresh live read。

### D. 预算、trace 与验证

1. 将 `tests/test_copy_readback_ledger.py` 的七个公开 Copy/Move 操作 × Page root/subtree 与容器路径预算按优化后实际数值更新；冻结 `(operation, read_reason)`，不只断言总数。
2. 增加 stale-preflight、mutation epoch 失效、root-only promotion、create allocation ID 冲突、delete partial/timeout/recycle metadata 不可见与 Page order ancestor coverage 的负向合同。
3. Debug Trace 继续只写 allowlisted `read_reason`、operation 和 content-free 计数；测试证明共享创建/删除服务路径不留下未归因的 hierarchy/Page read。
4. 用户在 disposable 本地 OneNote 数据上对比优化前后 trace：单页、Page subtree、至少一个容器 Move、source-drift/`copy_only` 负向路径；Agent 不执行真实 scenario。

## 非目标与安全边界

- 不减少必要的双稳定创建/删除收敛 observation，也不把“后端调用返回成功”当作持久化成功；
- 不合并跨 mutation epoch 的读取，不引入跨 tool call 缓存、TTL cache 或全局 mutable snapshot；后者仍属于 TODO 024；
- 不改变 fast 验证模式的产品决策与删源门；该方向仍属于 TODO 045 的独立工作范围；
- 不将本项的 call-count 降低归因于或绑定到 pywin32 transport；048 可以独立实施；
- 不增加并发 OneNote COM 调用，不改变 TODO 046/047 的锁与调度边界。

## 完成定义

- [ ] Copy/Move 共享创建、删除与 Page order XML 路径只接受类型化、epoch 校验的内部 snapshot，不持有或泄露跨调用 cache；
- [ ] 创建/删除前置重复 hierarchy 读取和可证明的 post-delete snapshot 重复读取已按实际实现减少，所有 mutation 后关键证据仍是 fresh live observation；
- [ ] readback ledger 冻结优化后的精确 `(backend operation, read_reason)` 预算，并覆盖 stale/partial/timeout/promotion 等负向路径；
- [ ] 自动化测试证明 strict fidelity、Copy-before-delete、source drift、typed partial outcome、recycle-bin 不确定性与 content-free trace 合同保持不变；
- [ ] 用户确认 disposable 本地 OneNote trace 对比：backend call 数按预期下降，且无 source 意外删除或验证降级；
- [ ] 当前设计文档、开发 ledger、TODO 045/048 的边界与索引已同步。

## 关联

- [TODO 045](045_copy_move_readback_snapshot_efficiency.md)：phase-local cache、strict readback 基线与 fast mode 的上层性能工作；049 是其共享服务重复 call 的窄化后续。
- [TODO 048](048_pywin32_persistent_com_bridge.md)：降低仍不可省略的 backend call transport 成本。
- [TODO 024](024_search_and_query_read_snapshot_cache.md)：跨 tool call 的短时只读缓存，明确不在本项范围。
- [TODO 046](046_scoped_mutation_coordination.md)：mutation footprint、锁与缓存失效范围需要保持一致。
- [Copy/Move read reason ledger](../dev/copy_move_read_reason_ledger.md)：本项的 trace 归因与预算依据。
- [Operation Runtime §8.1](../design/operation_runtime.md#81-copymove-phase-local-readback-snapshot045-strict-优化)：当前 phase-local snapshot 与 mutation epoch 合同。
