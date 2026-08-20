# 045：Copy/Move 回读 Snapshot 效率与快速验证模式

> ID：045
> 状态：进行中
> 优先级：P1
> 类型：性能 / Copy / Move / 回读验证 / 保真合同
> 更新日期：2026-08-20

## 决策摘要

Copy 与重建式 Move 的安全边界仍是 Copy-before-delete：默认严格验证必须在任何源对象删除前证明目标与既有内容、标题、拓扑合同一致。本 TODO 的目标不是把一次公开调用压缩成“source/target 各读取一次”的全局快照；创建、写入、排序与删除会改变 OneNote 的 live state，写后收敛和删源前 source-drift 检查必须保留新的证据。

优化优先级是：先消除同一无 mutation 证据阶段内重复的 `get_hierarchy`，再消除同一 Page live observation 被多个 comparator/报告重复解析或读取的情况。复用必须是显式、operation-local 且按 mutation 边界失效的 phase-local snapshot；不得引入跨 tool call、跨 mutation 或隐藏的全局缓存。

用户在启用本地 Debug Trace 后观察到 Copy/Move 仍有大量 readback backend call。该观察是本项的启动信号；实现前必须用 content-free trace 和确定性 fake 计数把基线、每个 read 的语义原因、目标上限及必要性冻结下来，不能仅凭一次日志推断所有资源类型的最优读取次数。

另评估可选的 fast 验证模式：仅比较精确 target 的存在、数量和必要层级/范围，不逐项运行完整 semantic comparator。它不是当前默认，也不能把“存在内容”自动标记为 `lossless`、`semantic_fidelity` 或 `copy_contract_satisfied`。若产品决定允许它影响 Move 的删源门，必须单独明确这种较弱证据的用户承诺、失败语义和真实验证矩阵；在此之前 fast 只可用于 Copy 或以 `copy_only` 保留 source 的 Move。

**2026-08-19 实施进度（strict 优化，不含 fast 模式与工作范围 D）**：已完成 read reason allowlist、`CopyReadCache`、backend operation 闭合分类与 mutation epoch、`CopyService` 通用路径接入、reconciliation→convergence 首样本合并、参数化 fake ledger 与相关 pytest。

**2026-08-19 审阅修复**：已按增量审阅关闭两项 P1 与两项 P2——`CopyReadCache` 改为 task-local `ContextVar`（并发/交错隔离合同）、ledger 覆盖全部公开 Copy/Move（含 `copy_notebook`/`move_section_group`）并冻结精确 `(operation, reason)` 预算、移除 `filesystem:` 前缀匹配改精确 `FILESYSTEM_OPERATIONS` allowlist、`confirm*` preflight 类型化为 `HierarchySnapshot` 并校验 epoch（陈旧则 live read）、snapshot 返回副本。待用户 human-gated 真实 trace 对比证据。

**2026-08-20**：共享创建/删除与 Page 排序 XML 的重复 live read 已由 [TODO 049](049_copy_move_backend_readback_call_deduplication.md) 收窄实现；`HierarchySnapshot` 下沉到 hierarchy 层，`CopyReadCache` 仍只保存普通同 epoch 读取。049 的真实 trace 验收与本项 human-gated 对比可以共用同一组 disposable 场景，但本项完成定义仍要求用户确认 045 基线对比。

## 已观测基线（非验收证据）

2026-08-19 的一份 content-free 本地 Debug Trace 中，23 次 `move_page` 调用显示：典型单页成功 Move 有 31 次 backend call（20 次 `get_hierarchy`、6 次 `get_page_content`、5 次写入类调用），耗时约 13–15 秒；较大的 Page scope 出现 71 与 91 次调用。该样本中 `get_hierarchy` 占 Move backend 等待时间约 65%，`get_page_content` 约 23%。

这证明优化空间主要在 hierarchy readback，而非只在 Page 正文比较。现有 trace 只暴露 COM operation，尚不能把每条 `get_hierarchy` 精确归因到确认、规划、创建收敛、排序、删源或收敛阶段；实现前须用 deterministic fake ledger 建立这种归因。该记录不构成真实 OneNote 验收或任何性能承诺。

优化后规划期 `get_hierarchy`（`source_confirmation`+`plan_capture`+`destination_precondition`）在 ledger 合同中已降为每个 read-only epoch 1 次；reorder 双读已消除。

## 当前缺口

- ~~`_capture_source()` 已在规划阶段保留 `page_xml` 并供 Copy transform 使用，但没有把 hierarchy 读数和后续只读消费者组织为显式 phase-local snapshot~~ → **已接入 `CopyReadCache`/`HierarchySnapshot`**；
- ~~`HierarchyService.resources()` 与 `resource()` 会各自触发完整 `get_hierarchy`~~ → **Copy/Move 路径经 cache 复用同一 epoch 的完整解析 snapshot**；
- Page 写前/写后/收敛读数：reconciliation 首样本可并入 convergence，其余跨 mutation 读仍独立；**每页 `get_page_content` 预期从 ≥4 次降至约 3 次**（待真实 trace 量化）；
- 容器专属批量化（工作范围 D）与 fast 模式（工作范围 F）**明确不在本轮**；
- ~~Runtime Debug Trace 尚未把 read reason 投影到 backend 行~~ → **已实现 allowlist 限定的 `read_reason` 字段**；
- 共享创建/删除与 `page_order_xml` 的重复 live read 已移交 [TODO 049](049_copy_move_backend_readback_call_deduplication.md) 并完成自动化侧实现；
- 用户 human-gated disposable fresh/cache 场景的优化前后 trace 对比**尚未完成**。

## 工作范围

### A. 建立阶段化调用基线与预算（先行） — **已完成（mock/ledger）**

1. 对七个公开 Copy/Move 操作建立确定性 fake backend ledger（`tests/test_copy_readback_ledger.py`）。
2. 11 类 read reason allowlist 与 CopyService 埋点（`read_reasons.py`）。
3. Debug Trace backend 行 `read_reason` 交叉核对。
4. Ledger 证明规划期 hierarchy 重复读已消除；写后/删源前 live read 保持独立。

### B. Phase-local hierarchy snapshot（第一优先级） — **已完成**

见 [Operation Runtime §8.1](../design/operation_runtime.md#81-copymove-phase-local-readback-snapshot045-strict-优化)。

### C. Page 内容快照与收敛读数（第二优先级） — **部分完成**

1. 规划 source `page_xml` 内存复用保留并扩展至 cache 派生槽。
2. target 侧同一 live observation 的 digest/等价比较/标题检查经 `PageContentDerivation` 集中派生。
3. reconciliation 成功后 `convergence(initial_value=...)` 已实施；负向合同由既有 `test_copying.py` 与 convergence 测试覆盖。
4. 跨 mutation 读（写前 pre-state、删源 drift、删源后验证）保持不复用。

### D. 批量化机会（仅在量化后） — **未开始（本轮不含）**

### E. 默认严格验证合同 — **保持不变**

### F. 可选 fast 验证模式（需独立产品决策） — **未开始（本轮不含）**

## 自动化与可观测性

- fake backend ledger 与 `test_copy_read_cache.py`、`test_backend_operation_classification.py` 已落地；
- Debug Trace `read_reason` 与 epoch 失效路径有 pytest 覆盖；
- 全量 `.venv\Scripts\python.exe -m pytest -q`：**1480 passed**（2026-08-19）。

## Human-gated 验证

用户在交互式前台终端执行（Agent 不得运行真实 `run.py <scenario>`）：

```powershell
# 1. 启用 content-free debug trace（可选：先跑一轮作为优化前基线，再拉取本分支后作为优化后对比）
$env:LOCAL_ONENOTE_MCP_DEBUG_TRACE = "true"
# 可选绝对路径；省略则使用用户本地默认目录
# $env:LOCAL_ONENOTE_MCP_DEBUG_DIR = "D:\trace\copy-move-045-after"

# 2. Fresh disposable 场景（各至少一次成功路径）
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page
.venv\Scripts\python.exe tests\manual_validation\run.py copy-section
.venv\Scripts\python.exe tests\manual_validation\run.py copy-section-group
.venv\Scripts\python.exe tests\manual_validation\run.py copy-notebook
.venv\Scripts\python.exe tests\manual_validation\run.py move-page
.venv\Scripts\python.exe tests\manual_validation\run.py move-section
.venv\Scripts\python.exe tests\manual_validation\run.py move-section-group

# 3. Cache 路径（materialized working copy）
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --use-cache
.venv\Scripts\python.exe tests\manual_validation\run.py move-page --use-cache

# 4. 对比 JSONL：统计各 tool call 的 backend 行数量、operation 分布，以及 Copy/Move 行的 read_reason 分布
#    重点：规划期 get_hierarchy 是否降为每 epoch 1 次；写后/删源前是否仍有独立 live read
```

- 覆盖 Page root-only/subtree、三类容器 Copy、至少一个成功 Move 与一个 source drift/`copy_only` 负向路径；
- 若 fast mode 获准，需单独验证；本轮不涉及。

## 非目标与安全边界

（不变，见原文。）

## 完成定义

- [x] 全部公开 Copy/Move 的 readback 基线、固定 read reason、evidence epoch 与 operation-local snapshot 消费者已冻结为确定性合同（mock/ledger）；
- [x] 同一 read-only evidence epoch 内的 hierarchy 重复读取已消除；解析、比较、typed failure 与报告均从该 epoch 的内存 snapshot 派生；
- [x] 每次可能改变状态的 backend mutation 均会使有关 snapshot 失效；写后收敛、Move 删源前 source drift/reconciliation 和删源后状态验证继续使用新的 live evidence（代码路径 + 负向 pytest）；
- [ ] Page Copy/Move 的 hierarchy/`get_page_content` 调用预算相对已冻结基线可解释地下降，且**用户确认**真实 disposable trace 证据；较大 subtree 的批量化（工作范围 D）未纳入；
- [x] 优化后 strict fidelity、typed failure、partial/timeout、source drift 与 Move 删源门语义保持不变，且有负向测试证明 source 保留；
- [x] Debug Trace 与 fake backend 证明 readback 调用有界、可解释，并且不泄露内容或标识（自动化侧）；
- [ ] 如实施 fast mode：本轮未实施；
- [x] 受影响设计文档、自动化测试已同步；具名 human-gated scenario 命令已整理；
- [ ] 用户确认 strict 优化的真实 disposable Copy/Move 证据。

## 关联

- [TODO 016](016_copy_page_manual_validation_read_evidence_efficiency.md)：历史 manual-validation 的 Page XML 读取证据降本；本项覆盖生产 Copy/Move readback。
- [TODO 035](035_copy_move_internal_planning_and_agent_role.md)：Copy/Move 内部 planning 与服务端证明职责。
- [TODO 040](040_move_readback_validation_followups.md)：已闭合的 Page fidelity/readback bug；本项不重开其缺陷，只优化读取复用与验证等级。
- [Operation Runtime §8.1](../design/operation_runtime.md#81-copymove-phase-local-readback-snapshot045-strict-优化)：phase-local snapshot、epoch 分类与 trace read_reason。
- [TODO 049](049_copy_move_backend_readback_call_deduplication.md)：共享创建/删除与 Page 排序 XML 的重复 live read 去重。
- [TODO 索引](README.md)：本条的状态、优先级与摘要必须同步维护。
