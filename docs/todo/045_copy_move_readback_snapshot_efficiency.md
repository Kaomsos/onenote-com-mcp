# 045：Copy/Move 回读 Snapshot 效率优化

> ID：045
> 状态：已完成
> 优先级：P1
> 类型：性能 / Copy / Move / 回读验证 / 保真合同
> 更新日期：2026-08-20

## 决策摘要

Copy 与重建式 Move 的安全边界仍是 Copy-before-delete：默认严格验证必须在任何源对象删除前证明目标与既有内容、标题、拓扑合同一致。本 TODO 的目标不是把一次公开调用压缩成“source/target 各读取一次”的全局快照；创建、写入、排序与删除会改变 OneNote 的 live state，写后收敛和删源前 source-drift 检查必须保留新的证据。

优化优先级是：先消除同一无 mutation 证据阶段内重复的 `get_hierarchy`，再消除同一 Page live observation 被多个 comparator/报告重复解析或读取的情况。复用必须是显式、operation-local 且按 mutation 边界失效的 phase-local snapshot；不得引入跨 tool call、跨 mutation 或隐藏的全局缓存。

用户在启用本地 Debug Trace 后观察到 Copy/Move 仍有大量 readback backend call。该观察是本项的启动信号；实现前必须用 content-free trace 和确定性 fake 计数把基线、每个 read 的语义原因、目标上限及必要性冻结下来，不能仅凭一次日志推断所有资源类型的最优读取次数。

2026-08-20 产品决策移除可选 fast 验证模式：当前单次调用耗时已经降到可接受范围，不再为继续压缩时间引入第二套较弱 fidelity 合同。Copy 与 Move 只保留默认 strict 验证，Move 的删源门继续要求完整 `copy_contract_satisfied`；不增加 fast 参数、模式分支或 `copy_only` 特例。

**2026-08-19 实施进度（strict 优化）**：已完成 read reason allowlist、`CopyReadCache`、backend operation 闭合分类与 mutation epoch、`CopyService` 通用路径接入、reconciliation→convergence 首样本合并、参数化 fake ledger 与相关 pytest。

**2026-08-19 审阅修复**：已按增量审阅关闭两项 P1 与两项 P2——`CopyReadCache` 改为 task-local `ContextVar`（并发/交错隔离合同）、ledger 覆盖全部公开 Copy/Move（含 `copy_notebook`/`move_section_group`）并冻结精确 `(operation, reason)` 预算、移除 `filesystem:` 前缀匹配改精确 `FILESYSTEM_OPERATIONS` allowlist、`confirm*` preflight 类型化为 `HierarchySnapshot` 并校验 epoch（陈旧则 live read）、snapshot 返回副本。待用户 human-gated 真实 trace 对比证据。

**2026-08-20 完成决策**：共享创建/删除与 Page 排序 XML 的重复 live read 已由 [TODO 049](049_copy_move_backend_readback_call_deduplication.md) 收窄实现；`HierarchySnapshot` 下沉到 hierarchy 层，`CopyReadCache` 仍只保存普通同 epoch 读取。TODO 049 的用户真实 trace 已证明双页 Move 从 43 降至 33 个 backend call，七个公开 Copy/Move 的 16 次调用全部完成且 strict fidelity/Copy-before-delete 未降级。用户确认当前单次调用时间已经足够低，因此 045 以 strict 优化完成；fast 模式从产品目标移除，更复杂的层级化缓存与失效范围并入 [TODO 046](046_scoped_mutation_coordination.md) 的层级化写保护锁设计。

## 已观测基线（非验收证据）

2026-08-19 的一份 content-free 本地 Debug Trace 中，23 次 `move_page` 调用显示：典型单页成功 Move 有 31 次 backend call（20 次 `get_hierarchy`、6 次 `get_page_content`、5 次写入类调用），耗时约 13–15 秒；较大的 Page scope 出现 71 与 91 次调用。该样本中 `get_hierarchy` 占 Move backend 等待时间约 65%，`get_page_content` 约 23%。

这证明优化空间主要在 hierarchy readback，而非只在 Page 正文比较。现有 trace 只暴露 COM operation，尚不能把每条 `get_hierarchy` 精确归因到确认、规划、创建收敛、排序、删源或收敛阶段；实现前须用 deterministic fake ledger 建立这种归因。该记录不构成真实 OneNote 验收或任何性能承诺。

优化后规划期 `get_hierarchy`（`source_confirmation`+`plan_capture`+`destination_precondition`）在 ledger 合同中已降为每个 read-only epoch 1 次；reorder 双读已消除。

## 收尾边界

- ~~`_capture_source()` 已在规划阶段保留 `page_xml` 并供 Copy transform 使用，但没有把 hierarchy 读数和后续只读消费者组织为显式 phase-local snapshot~~ → **已接入 `CopyReadCache`/`HierarchySnapshot`**；
- ~~`HierarchyService.resources()` 与 `resource()` 会各自触发完整 `get_hierarchy`~~ → **Copy/Move 路径经 cache 复用同一 epoch 的完整解析 snapshot**；
- Page 写前/写后/收敛读数：reconciliation 首样本已并入 convergence，其余跨 mutation 读仍独立；这些 fresh evidence 是安全边界，不再作为 045 的待优化缺口；
- fast 模式已移除，不保留后续实现入口；容器/层级级别的进一步 snapshot、缓存合并和失效粒度只允许与 TODO 046 的写保护 footprint 一起评估；
- ~~Runtime Debug Trace 尚未把 read reason 投影到 backend 行~~ → **已实现 allowlist 限定的 `read_reason` 字段**；
- 共享创建/删除与 `page_order_xml` 的重复 live read 已移交 [TODO 049](049_copy_move_backend_readback_call_deduplication.md) 并完成自动化侧实现；
- 用户 human-gated disposable Copy/Move 与 content-free trace 对比已由 TODO 049 完成并确认。

## 工作范围

### A. 建立阶段化调用基线与预算（先行） — **已完成（mock/ledger）**

1. 对七个公开 Copy/Move 操作建立确定性 fake backend ledger（`tests/test_copy_readback_ledger.py`）。
2. 11 类 read reason allowlist 与 CopyService 埋点（`read_reasons.py`）。
3. Debug Trace backend 行 `read_reason` 交叉核对。
4. Ledger 证明规划期 hierarchy 重复读已消除；写后/删源前 live read 保持独立。

### B. Phase-local hierarchy snapshot（第一优先级） — **已完成**

见 [Operation Runtime §8.1](../design/operation_runtime.md#81-copymove-phase-local-readback-snapshot045-strict-优化)。

### C. Page 内容快照与收敛读数（第二优先级） — **已完成（安全边界内）**

1. 规划 source `page_xml` 内存复用保留并扩展至 cache 派生槽。
2. target 侧同一 live observation 的 digest/等价比较/标题检查经 `PageContentDerivation` 集中派生。
3. reconciliation 成功后 `convergence(initial_value=...)` 已实施；负向合同由既有 `test_copying.py` 与 convergence 测试覆盖。
4. 跨 mutation 读（写前 pre-state、删源 drift、删源后验证）保持不复用。

### D. 更复杂的层级化缓存 — **移交 TODO 046**

跨调用、跨层级或按资源子树复用 snapshot 会同时改变 mutation 写保护和失效范围，不能作为 Copy/Move 的独立性能开关。该方向已并入 TODO 046；045 不再保留容器专属批量化或隐藏 cache 目标。

### E. 默认严格验证合同 — **保持不变**

### F. 可选 fast 验证模式 — **已移除**

不实现、不暴露，也不预留影响 Move 删源门的较弱验证等级。

## 自动化与可观测性

- fake backend ledger 与 `test_copy_read_cache.py`、`test_backend_operation_classification.py` 已落地；
- Debug Trace `read_reason` 与 epoch 失效路径有 pytest 覆盖；
- 全量 `.venv\Scripts\python.exe -m pytest -q`：**1480 passed**（2026-08-19）。

## Human-gated 验证（已完成）

用户已确认 disposable 本地 OneNote 的七个公开 Copy/Move 场景与 content-free trace。TODO 049 保存的同组证据显示：双页、非 promotion `move_page` 从基线 43 个 backend call 降至 33 个；root-only promotion 为 26 个 backend call并保留两次 fresh delete confirmation；七个公开操作的 16 次调用均完成，未观察到 source 意外删除或 strict fidelity 降级。Agent 未执行真实 scenario。

## 非目标与安全边界

（不变，见原文。）

## 完成定义

- [x] 全部公开 Copy/Move 的 readback 基线、固定 read reason、evidence epoch 与 operation-local snapshot 消费者已冻结为确定性合同（mock/ledger）；
- [x] 同一 read-only evidence epoch 内的 hierarchy 重复读取已消除；解析、比较、typed failure 与报告均从该 epoch 的内存 snapshot 派生；
- [x] 每次可能改变状态的 backend mutation 均会使有关 snapshot 失效；写后收敛、Move 删源前 source drift/reconciliation 和删源后状态验证继续使用新的 live evidence（代码路径 + 负向 pytest）；
- [x] Page Copy/Move 的 backend 调用预算相对冻结基线可解释地下降，且用户确认真实 disposable trace；更复杂的层级化缓存已移交 TODO 046；
- [x] 优化后 strict fidelity、typed failure、partial/timeout、source drift 与 Move 删源门语义保持不变，且有负向测试证明 source 保留；
- [x] Debug Trace 与 fake backend 证明 readback 调用有界、可解释，并且不泄露内容或标识（自动化侧）；
- [x] 产品决定移除 fast mode，公开调用继续只有 strict fidelity 合同；
- [x] 受影响设计文档、自动化测试已同步；具名 human-gated scenario 命令已整理；
- [x] 用户确认 strict 优化后的真实 disposable Copy/Move 证据，并确认当前单次调用耗时无需新增 fast 模式。

## 关联

- [TODO 016](016_copy_page_manual_validation_read_evidence_efficiency.md)：历史 manual-validation 的 Page XML 读取证据降本；本项覆盖生产 Copy/Move readback。
- [TODO 035](035_copy_move_internal_planning_and_agent_role.md)：Copy/Move 内部 planning 与服务端证明职责。
- [TODO 040](040_move_readback_validation_followups.md)：已闭合的 Page fidelity/readback bug；本项不重开其缺陷，只优化读取复用与验证等级。
- [Operation Runtime §8.1](../design/operation_runtime.md#81-copymove-phase-local-readback-snapshot045-strict-优化)：phase-local snapshot、epoch 分类与 trace read_reason。
- [TODO 049](049_copy_move_backend_readback_call_deduplication.md)：共享创建/删除与 Page 排序 XML 的重复 live read 去重。
- [TODO 索引](README.md)：本条的状态、优先级与摘要必须同步维护。
