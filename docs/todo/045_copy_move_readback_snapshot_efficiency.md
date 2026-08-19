# 045：Copy/Move 回读 Snapshot 效率与快速验证模式

> ID：045
> 状态：待办
> 优先级：P1
> 类型：性能 / Copy / Move / 回读验证 / 保真合同
> 更新日期：2026-08-19

## 决策摘要

Copy 与重建式 Move 的安全边界仍是 Copy-before-delete：默认严格验证必须在任何源对象删除前证明目标与既有内容、标题、拓扑合同一致。本 TODO 的目标是消除同一公开调用中为不同 comparator、阶段或报告重复发出的 Page/层级 readback：对每个必要 scope 读取一次完整、受预算约束的 source/target snapshot，再在内存中派生全部比较、typed failure 与报告。

用户在启用本地 Debug Trace 后观察到 Copy/Move 仍有大量 readback backend call。该观察是本项的启动信号；实现前必须用 content-free trace 和确定性 fake 计数把基线、目标上限及每类调用的必要性冻结下来，不能仅凭一次日志推断所有资源类型的最优读取次数。

另评估可选的 fast 验证模式：仅比较精确 target 的存在、数量和必要层级/范围，不逐项运行完整 semantic comparator。它不是当前默认，也不能把“存在内容”自动标记为 `lossless`、`semantic_fidelity` 或 `copy_contract_satisfied`。若产品决定允许它影响 Move 的删源门，必须单独明确这种较弱证据的用户承诺、失败语义和真实验证矩阵；在此之前 fast 只可用于 Copy 或以 `copy_only` 保留 source 的 Move。

## 当前缺口

- Page Copy/Move 的 source→transformed→target 比较、typed content failure、标题/拓扑确认和报告可能分别触发后端读回，而非复用同一份完整 snapshot；
- 容器 Copy/Move 与 Page subtree 的 readback 范围、读取次数和内存派生边界没有统一的公开性能合同；
- Runtime Debug Trace 已能以 content-free backend 行观察调用，但尚未把 Copy/Move 的预期 readback budget、每个 snapshot 的消费者和实际 call 上限冻结为自动化证据；
- 当前严格验证适合保真与删源安全，但在已由受控测试充分覆盖的低风险归档场景中，尚未定义一个显式、可审计且不冒充完整语义保真的较弱验证选择。

## 工作范围

### A. Snapshot 复用与调用预算

1. 对 `copy_page`、`copy_section`、`copy_section_group`、`copy_notebook` 及对应的重建式 `move_page`、`move_section`、`move_section_group` 建立每个阶段的 backend readback 清单；区分 source capture、target readback、层级确认、convergence/reconciliation 和报告派生。
2. 在 exact ID、已授权且受现有 `CopyBudget` 限制的 scope 内，建立不可变的 operation-local snapshot 对象。每个 Page 必要时读取一次完整 source snapshot、一次完整 target snapshot；所有内容 comparator、typed mismatch、title/拓扑投影和报告只能消费内存中的同一份快照。
3. 对容器与 subtree，按完整选中 scope 批量读取并用 ID 建索引；禁止为每个子 comparator、每个失败类别或每个 report 字段重新查询同一对象。不得以无界全 Notebook 扫描换取“少一次 Page read”。
4. 只在协议确实需要新的 live 证据时读取：例如 mutation 后 target 读取、Move 删源前 source drift/reconciliation。每一类额外读取必须有稳定原因、受预算限制，并能在 content-free trace 中与最终计数对账。
5. 维持 operation 间隔离：不得把 mutation 前后的 snapshot 作为跨调用缓存复用，也不得用 stale snapshot 绕过 Move 的 source drift、exact ID 或收敛检查。

### B. 默认严格验证合同

- 默认模式继续执行现有 source→transformed→target 的完整 semantic、标题、拓扑与 typed failure 合同；优化只能减少重复 I/O，不能缩小比较维度、隐藏 mismatch 或改变成功 response 的 fidelity 含义。
- Move 只有在严格 target readback、source drift/reconciliation 和现有 `verified/lossless/copy_contract_satisfied` 门全部满足后才能删除 source；任一 snapshot 缺失、预算耗尽、读取失败或内存比较不一致都继续 `copy_only`/partial 并保留 source。
- Snapshot 的输入、ID 映射、预算、内容脱敏、局部失败和不重放语义继续复用当前 Runtime/Copy 规则；不引入第二套 Copy/Move 执行模型。

### C. 可选 fast 验证模式（需独立产品决策）

- 先决定公开形态（例如显式 `verification_mode`），默认必须是严格模式；不得因环境变量、调用量、timeout 或 Agent 推测隐式降级。
- fast 至少以 exact allocated/remapped target ID、预期资源类型、选中 scope cardinality、必要 parent/section 归属和 active/recycle-bin 状态验证“有无与数量”；对象数量相等不代表正文、标题、顺序、子树或 binary 相等。
- 返回必须清晰投影验证等级及未验证维度，例如 `verification_level=presence_and_count`；不得返回或沿用 `lossless=true`、`semantic_fidelity=true`、`copy_contract_satisfied=true` 等完整保真结论。
- 在没有新的用户产品决策与独立真实证据前，fast Move 必须保留 source 并返回 `copy_only`/待人工确认结果。若未来允许 fast Move 删源，必须新增独立授权/confirmation、显式文案、恢复路径和失败矩阵；不得把既有严格 Move 的真实测试证据扩张为此结论。
- fast mode 不是对内容无损的证明；“只要有内容即语义保真”只能作为待验证的产品假设，不能由现有测试或 mock 自动推出。

## 自动化与可观测性

- 用 fake backend 冻结每种 Copy/Move、root-only 与 subtree 的 source/target snapshot 调用上限；同一 Page 的所有 strict comparator 必须证明共用同一快照，而非仅断言总结果相同。
- 覆盖 source/target snapshot 缺失、读取失败、预算超限、ID remap、重复标题、content mismatch、title/topology mismatch、source drift、partial 和 timeout，证明优化后不读取更多、不掩盖错误且 Move 不删源。
- 使用 Debug Trace 的 content-free backend 行验证调用数量、顺序和固定内部 operation 分类；trace 不得记录 Page 内容、ID、路径、标题、snapshot 数据或比较细节。
- fast mode（若实施）覆盖 target 缺失、数量不符、错误 parent/type、recycle-bin 状态、子树范围不符和 source 保留；证明默认调用未传显式模式时仍走 strict 合同。
- 聚焦自动化通过后运行完整 `.venv\Scripts\python.exe -m pytest -q`。Agent 只能运行纯测试与显式 `--dry-run`，不能启动真实 OneNote scenario。

## Human-gated 验证

- 用户在 disposable fresh/cache Copy/Move 场景中比较优化前后的 content-free trace：记录 backend call 分类与数量，不提交对象 ID、标题、正文或完整路径；
- 覆盖 Page root-only、Page subtree、Section、SectionGroup 和 Notebook Copy，及至少一个成功 Move 和一个 readback/source drift 负向路径；
- 若 fast mode 获准，用户必须单独验证 Copy 的显式 fast 投影；任何允许删源的 fast Move 需要独立的 human-gated 场景、明确确认和源保留负向证据。

## 非目标与安全边界

- 不删除、重写或弱化当前默认 strict fidelity 合同；
- 不把受控 fixture/mock 的成功扩大为任意 OneNote 内容的语义保真结论；
- 不直接编辑 `.one` 文件、不引入云端/遥测，也不记录 OneNote 内容或原始 bridge payload；
- 不以性能理由绕过 mutation policy、exact ID、confirmation、CopyBudget、source drift/reconciliation 或 Move 的 Copy-before-delete 门；
- 不新增会由 pytest、CI、hook、timer、watcher 或 Agent 自动执行的真实 mutation 验证。

## 完成定义

- [ ] 全部公开 Copy/Move 的 readback 基线、必要读取原因和 operation-local snapshot 消费者已冻结为确定性合同；
- [ ] 严格模式对每个必要 Page/source/target scope 只读取一次完整 snapshot，并在内存中完成全部相应比较与报告派生；
- [ ] 优化后 strict fidelity、typed failure、partial/timeout、source drift 与 Move 删源门语义保持不变，且有负向测试证明 source 保留；
- [ ] Debug Trace 与 fake backend 证明 readback 调用数量有界、可解释，并且不泄露内容或标识；
- [ ] 如实施 fast mode：其为显式 opt-in、默认 strict、准确标记较弱验证等级，并且未获单独批准前不允许 fast Move 删源；
- [ ] 受影响设计、工具契约、双语配置/README、自动化测试和具名 human-gated scenario 同步完成；
- [ ] 用户确认 strict 优化的真实 disposable Copy/Move 证据；若 fast mode 影响删源，另有该模式专属的用户确认与负向源保留证据。

## 关联

- [TODO 016](016_copy_page_manual_validation_read_evidence_efficiency.md)：历史 manual-validation 的 Page XML 读取证据降本；本项覆盖生产 Copy/Move readback。
- [TODO 035](035_copy_move_internal_planning_and_agent_role.md)：Copy/Move 内部 planning 与服务端证明职责。
- [TODO 040](040_move_readback_validation_followups.md)：已闭合的 Page fidelity/readback bug；本项不重开其缺陷，只优化读取复用与验证等级。
- [Operation Runtime](../design/operation_runtime.md)：backend call 计数与 Debug Trace 事件模型。
- [TODO 索引](README.md)：本条的状态、优先级与摘要必须同步维护。
