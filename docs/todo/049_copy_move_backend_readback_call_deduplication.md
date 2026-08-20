# 049：Copy/Move backend readback 调用去重

> ID：049
> 状态：已完成
> 优先级：P2
> 类型：性能 / Copy / Move / strict readback / backend call
> 更新日期：2026-08-20

## 决策摘要

TODO 045 已交付调用专属、按 mutation epoch 失效的 `CopyReadCache`，消除了 CopyService 自身同一只读阶段的重复读取。本项处理仍由共享 `MutationService` 和 hierarchy XML 构造辅助路径绕开该 snapshot 所产生的重复 backend call。

优化不得降低 strict Copy fidelity、创建/删除收敛、Move 的 Copy-before-delete、source drift 检查或非永久删除的 typed outcome 证据。目标是让已由同一 epoch 的完整 hierarchy snapshot 证明的信息以类型化内部参数传递，而不是再次调用 OneNote；每次状态改变后仍必须重新获取 live evidence。

这与 [TODO 048](048_persistent_com_client_bridge.md) 相互独立：048 降低每次 backend call 的 transport 固定成本；本项减少需要发出的 backend call 数量。

**2026-08-20 完成证据**：中性 `HierarchySnapshot`、集中 `_resolve_full_preflight`、创建/排序 catalog 去重、fresh 删除 confirmation（不入 cache）、`UNSET`/`None` convergence、MutationService 私有 delete observation，以及七工具轻量 ledger + 三条共享服务组合路径均已落地。root-only promotion 在收敛验证后只从该 observation 重绑源 root 的 `modified`，标题/Section 仍用 source-drift/plan 确认值，第二次 fresh confirmation 继续拦截随后的外部 drift；私有 clock 不进入公开 `preserved_descendants`。自动化验证：聚焦合同、`tests/manual_validation/tests`、七个 Copy/Move scenario `--dry-run --json` 与全量 pytest **1513 passed**。用户已确认 disposable 本地 OneNote 手动测试通过；2026-08-20 content-free Debug Trace 也记录到七个公开 Copy/Move 操作共 16 次调用全部完成。

## 已观察基线（非验收证据）

2026-08-19 的本地 content-free Debug Trace 中，严格单页 `move_page` 成功路径稳定为 25 个 backend call，其中 16 次 `get_hierarchy`、4 次 `get_page_content`、5 次写入类调用。多页路径符合约 `18 × Page 数 + 7` 的线性关系：2/3/5 页分别为 43/61/97 次 backend call。该日志只证明 `move_page` 样本，不证明 Copy 与容器路径。

Read reason 将可合并的层级读取定位为：

- 每个创建对象有两次 `destination_precondition`；共享创建服务分别读取 parent/resource 和完整 hierarchy；
- 每个待删 Page 有两次 `delete_confirmation`；Move 已有 source-drift snapshot，但 `delete_page()` 和其内部 `delete_resource()` 又各自读取；
- Page 排序前的 `topology_verification` 中，`page_order_xml()` 为构造 ancestor XML 再读取一次 hierarchy，尽管 caller 已持有同 epoch catalog；
- 单页删除后有五次 `delete_convergence` hierarchy read，其中 reconciliation observation 与 Page scope final check 存在可验证的 observation 复用机会。最终 destination-position **不得**复用删除 snapshot。

上述数字只作为实现前的本地样本，不构成对所有 OneNote 数据形态或端到端耗时的承诺。

## 工作范围

### A. 创建前置 snapshot 传递 — **已实现（自动化合同）**

1. `create_page`、`create_section`、`create_section_group` 与 `create_notebook` 接受仅内部使用的 keyword-only `preflight: HierarchySnapshot | None`。
2. 无 mutation 的同一 epoch 内，CopyService 已持有的 snapshot 同时满足 parent/resource 校验和含回收站 `before_ids`；不传递可变 cache 本体。
3. `MutationService._resolve_full_preflight()` 集中校验 `start_id == ""`、`scope == "pages"` 且 `epoch == current_mutation_epoch()`；缺失或不匹配时 fresh fallback。
4. 创建后的 `wait_for_created` 双稳定观察仍保持 live read。

### B. Move 删除前置与删除后 observation 复用 — **已实现（自动化合同）**

1. 每次源拓扑 mutation 前直接调用 hierarchy 层 fresh snapshot，并显式安装 `DELETE_CONFIRMATION`；该读取不查询、不写回、不替换 cache。source-drift snapshot **不得**授权随后的 delete。confirmation 字段仍来自 drift/plan，不得从 fresh snapshot 重绑 title/section。
2. root-only promotion 严格执行双门：`source drift → fresh confirmation（promotion 前）→ update_hierarchy → fresh confirmation（delete 前）→ delete`。两份 snapshot 不得跨门复用。promotion 后只从已验证 observation 重绑源 root 的 `modified`；标题和 Section 不重绑，不得从第二次 fresh delete preflight 重绑。
3. `converge(..., initial_value=UNSET)`：省略参数不是首样本；显式 `None` 是合法“对象已消失”首样本。仅当 `MutationAttemptOutcome.applied` 且 reconciliation 满足同一 postcondition 时传入。partial、timeout、COM error 与 recycle-bin metadata 不可见保持原 typed outcome。
4. Page scope final check 只复用 MutationService 私有的最后一次 delete 后、已稳定且 object/epoch 匹配的 observation。公开 `delete_*` 不携带 snapshot，CopyService 不感知 `_DeleteObservation`。**明确不复用**最终 destination-position snapshot；CopyService 继续 fresh-read projection。

### C. 排序 XML 的 catalog 注入 — **已实现（自动化合同）**

1. `page_order_xml(..., catalog=...)` 对齐 `container_order_xml`，但门限更严：catalog ID 唯一、Section/ancestor 完整且 active；输入 Page ID 无重复、每项 `page.section_id == section.id`，并与该 Section 全部 active Page ID 集精确相等。`parent_page_id != None` 的嵌套 Page 合法。
2. 未提供 catalog 时保持现有独立读取。
3. 排序 mutation 后的 topology convergence 继续 fresh live read。

### D. 预算、trace 与验证 — **已完成**

1. 七个公开 Copy/Move 操作的轻量 `(operation, read_reason)` budget ledger 已按优化后数值更新。
2. 另有三条 scripted fake-bridge 组合路径：普通 Page Move、root-only promotion、一个容器 Move；走真实共享服务栈，不 monkeypatch `BaseService.call()`。
3. 负向合同覆盖 stale preflight、source drift 与首个 mutation 之间的 GUI 式 hierarchy 变化、promotion 成功后第二次 confirmation 失败（含 promotion 后 modified-only 外部 drift）、reconciliation `None` 后 reversion，以及既有 `copy_only` / recycle / partial outcome。
4. 用户已在 disposable 本地 OneNote 数据上对比优化前后 trace；Agent 未执行真实 scenario。`session-20260820T015815-32288-30d8e4fe.jsonl` 的双页、非 promotion `move_page` 为 33 个 backend call，低于 2026-08-19 基线中的 43 个（减少 10 次 `get_hierarchy`）；同一 session 的 root-only promotion 路径为 26 个 backend call，含两次 fresh `delete_confirmation` 和一次 `delete_hierarchy`。七个公开 Copy/Move 操作的 16 次 trace 调用均以 `tool_call.completed` 结束，读取均带 allowlisted reason。

## 非目标与安全边界

- 不减少必要的双稳定创建/删除收敛 observation，也不把“后端调用返回成功”当作持久化成功；
- 不合并跨 mutation epoch 的读取，不引入跨 tool call 缓存、TTL cache 或全局 mutable snapshot；后者仍属于 TODO 024；
- 不改变 fast 验证模式的产品决策与删源门；该方向仍属于 TODO 045 的独立工作范围；
- 不将本项的 call-count 降低归因于或绑定到任何特定 COM client adapter；048 可以独立实施；
- 不增加并发 OneNote COM 调用，不改变 TODO 046/047 的锁与调度边界；
- `mutation_epoch` 不能代表外部一致性；fresh confirmation 不提供跨进程原子性。

## 完成定义

- [x] Copy/Move 共享创建、删除与 Page order XML 路径只接受类型化、epoch 校验的内部 snapshot，不持有或泄露跨调用 cache；
- [x] 创建/删除前置重复 hierarchy 读取和可证明的 post-delete observation 重复读取已按实际实现减少，所有 mutation 后关键证据仍是 fresh live observation；
- [x] readback ledger 冻结优化后的精确 `(backend operation, read_reason)` 预算，并覆盖 stale/partial/timeout/promotion 等负向路径（自动化侧）；
- [x] 自动化测试证明 strict fidelity、Copy-before-delete、source drift、typed partial outcome、recycle-bin 不确定性与 content-free trace 合同保持不变；
- [x] 用户确认 disposable 本地 OneNote trace 对比：backend call 数按预期下降，且无 source 意外删除或验证降级；
- [x] 当前设计文档、开发 ledger、TODO 045 边界与索引已同步（不改动用户正在修改的 048 正文）。

## Human-gated 验收（已完成）

用户已在交互式前台对全部七个 disposable 场景完成手动验证，并确认无 source 意外删除或 strict fidelity 降级。Agent 未执行真实 `run.py <scenario>` / `run.py all`。content-free trace 的调用顺序、总数与 `(operation, read_reason)` 复核见上节；其中 `move_page` 覆盖 root-only promotion，确认每次 delete 前仍有 fresh confirmation。

## 关联

- [TODO 045](045_copy_move_readback_snapshot_efficiency.md)：phase-local cache、strict readback 基线与 fast mode 的上层性能工作；049 是其共享服务重复 call 的窄化后续。
- [TODO 048](048_persistent_com_client_bridge.md)：降低仍不可省略的 backend call transport 成本。
- [TODO 024](024_search_and_query_read_snapshot_cache.md)：跨 tool call 的短时只读缓存，明确不在本项范围。
- [TODO 046](046_scoped_mutation_coordination.md)：mutation footprint、锁与缓存失效范围需要保持一致。
- [Copy/Move read reason ledger](../dev/copy_move_read_reason_ledger.md)：本项的 trace 归因与预算依据。
- [Operation Runtime §8.1](../design/operation_runtime.md#81-copymove-phase-local-readback-snapshot045-strict-优化)：当前 phase-local snapshot、fresh confirmation 与 mutation epoch 合同。
