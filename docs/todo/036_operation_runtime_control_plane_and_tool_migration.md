# 036：Operation Runtime 操作执行控制层与 Tool 迁移

> ID：036
> 状态：已完成
> 优先级：P1
> 类型：生产架构 / Tool 执行控制面 / 一致性与可观测性
> 更新日期：2026-08-15

## 决策摘要

将当前分散在 Tool adapter、`tools.responses.invoke()`、进程内读写协调器、各 Service、公共 convergence/reconciliation helper 和 response mapper 中的跨切面执行规则，提升为 transport-independent 的 **Operation Runtime（操作执行控制层）**。大部分公开 MCP Tool 应通过同一个 Runtime 完成准入、授权、协调、deadline/预算、cache 一致性、执行策略、错误标准化、安全审计和结果收敛，再由 operation-specific Handler 保留实际 OneNote、filesystem 或 UI 业务语义。

本 TODO 推广统一执行模型，但不把所有 Tool 强行建模为 mutation。Runtime 下至少区分 Read、Mutation、Lifecycle、Filesystem Effect 与 UI Effect；只有真正改变 OneNote 持久状态的操作使用 `logical_ready → execute according to operation policy → reconcile actual outcome → converge → validate postcondition` 的完整 mutation 状态机。

本 TODO 不吸收或替代 [TODO 029](029_mcp_mutation_readiness_and_reconciliation_hardening.md)。TODO 029 以 `reparent_page` 完成 execute-once、四态 reconciliation、reconciled success、恢复建议和生命周期负合同，并交付 `MutationAttemptPolicy/Executor/Outcome` 作为 principal-attempt 原语。036 负责把这些原语组合进 operation-wide `MutationExecutionStrategy`，增加 admission、coordination、deadline、全 backend-call accounting、Registry、saga 与统一 Outcome。Operation Runtime 的全 Tool 推广不得成为 TODO 029 闭环的前置阻塞。

### 029 交接基线（2026-08-15）

TODO 029 已完成，不再是 036 的前置阻塞。036 启动时应直接复用而非重建以下基线：

- [`mutation_control.py`](../../src/local_onenote_mcp/services/mutation_control.py) 的 `MutationAttemptPolicy/Executor/Outcome` 与四态 reconciliation；
- [`operations.py`](../../src/local_onenote_mcp/services/operations.py) 的 `MUTATION_ATTEMPT_POLICY_BINDINGS`，仅作为迁移 inventory 输入，最终由唯一 Registry 取代；
- [`mutation_readiness_and_call_design.md`](../design/mutation_readiness_and_call_design.md) 与 [`tool_contracts.md`](../design/tool_contracts.md) 的 execute-once、identity、replay、recovery 和 content-free 合同；
- `1000 passed` 的完整纯测试基线，以及用户确认的 Reparent fresh/cache、canonical Rename、完整 convergence/production Close handoff 真实证据；
- production Close→pre-closed lease handoff 的约束：只消费 exact ID/name、双稳定、单次执行且未重放的 durable evidence，Runtime 迁移不得重新引入二次 Close。

这份交接在阶段 A 启动时只证明 036 可以开始，不曾被当作 Operation Runtime、Registry 或迁移阶段已经实现的完成证据；后续实现与验收证据见本文完成定义和当前实施证据。

## 背景与当前缺口

当前已有可靠基础：

- 所有公开 Tool 由薄 adapter 进入统一 response wrapper；
- `ReadWriteCoordinator` 为同一 MCP 进程提供 shared read 与 exclusive mutation lease；
- `MutationPolicy`、Copy/Search budget、typed HRESULT、`OneNoteError` 与 `PartialFailure` 已存在；
- 公共 `converge()` 和 `reconcile_mutation()` 已被多个 mutation 使用；
- Service 层已经拥有 typed ID、confirmation、operation-specific topology/content/fidelity 验证；
- Bridge 使用结构化 transport，并保持 local-only 与不直接编辑 `.one` 的边界。

当前控制协议仍主要是分散的命令式编排：

- Tool 是否取得 read/mutation lease 由 adapter 传入布尔值决定；
- policy、preflight、execute、observe、reconciliation、convergence 和 recovery 的调用顺序由各 Service 自行维持；
- operation 的 coordination、cache、budget、identity、attempt/replay、audit 和验证策略没有统一可审计目录；
- Read、Mutation、Sync、Close、Publish 和 Navigate 的结果语义不同，但缺少共同的上层 Operation 状态与错误边界；
- 新 Tool 容易直接复用 Service/Bridge 而遗漏统一协调、预算、审计或恢复合同；
- 当前成功响应和失败 envelope 已较稳定，但内部执行阶段、backend 类型和 safe diagnostics 尚未形成 transport-independent outcome。

因此需要统一的是**执行控制协议**，而不是把所有业务实现集中到一个 God Object，也不是让所有操作使用同一 postcondition 或 replay policy。

## 目标架构与命名

架构概念命名为 **Operation Control Plane**，核心运行对象命名为 `OperationRuntime`。推荐依赖方向：

```text
MCP Tool Adapter
  → OperationRuntime
    → OperationSpec / OperationRegistry
    → ExecutionStrategy
    → OperationHandler / existing services
      → Backend port
        → OneNote COM bridge | filesystem | Windows UI/process
```

核心对象职责：

| 对象 | 定位 |
| --- | --- |
| `OperationSpec` | 静态操作说明书：kind、capability、coordination、backend、budget、cache、retry 与 audit policy。 |
| `OperationRegistry` | Tool/operation 到 Spec、Strategy、Handler 的唯一登记和启动时完整性审计入口。 |
| `OperationExecution` | 单次调用的 content-free 控制面状态：stage、deadline、attempts、backend calls、completed steps。 |
| `OperationRuntime` | 执行状态机：准入、授权、协调、dispatch、finalize、lease 释放和 Outcome 生成。 |
| `ExecutionStrategy` | Read、Mutation、Lifecycle、Filesystem Effect、UI Effect 的不同流程模板。 |
| `OperationHandler` | operation-specific 业务实现；继续拥有 typed hierarchy、Page、Copy/Move 等语义。 |
| `OperationOutcome` | transport-independent 成功/失败、阶段、retry safety、recommended action 与 safe diagnostics。 |
| `MutationExecutionStrategy` | 复用 029 的 `MutationAttemptPolicy/Executor/Outcome`，并在 operation 范围承载 logical preflight、identity、attempt/replay、四态对账、saga/checkpoint 与 postcondition；不得把 attempt outcome 冒充完整 operation outcome。 |

首版可以使用 dataclass、Protocol、纯函数和少量 Strategy，避免为每个阶段创建独立 Service。Runtime 不直接构造 OneNote XML、不解释 Page 内容、不拥有 Copy fidelity，也不记录原始参数或 bridge payload。

## 统一执行阶段

统一阶段名称至少包括：

```text
admission
platform_preflight
authorization
coordination
preflight
execute
observe
reconcile
converge
postcondition
finalize
```

并非每类 Operation 都经过全部阶段：

- Read：`admission → authorization → coordination → execute → finalize`；
- Mutation：`admission → authorization → coordination → preflight → execute → observe → reconcile → converge → postcondition → finalize`；
- Lifecycle：按 Open/Close/Sync 的真实可观察性分别定义，不把 `SyncHierarchy accepted` 写成 completed；
- Filesystem Effect：验证 target/overwrite、执行和文件系统结果，不套用 OneNote identity 模型；
- UI Effect：验证 typed target 和 action accepted，不声称不可观察的持久状态。

所有出口都必须释放 lease，并返回非空 operation、stage、backend category 和可行动错误信息；Mutation 另返回 attempts、replayed、observed outcome、retry safety 和 recovery action。

## Operation 分类与推广边界

### 完整 Mutation Strategy

- Create；
- Page title/content mutation；
- Rename、Reorder；
- Page/Section/SectionGroup Reparent；
- Copy 与重建式 Move；
- Delete；
- Close 中可按 open-state 证明的 mutation/lifecycle 部分。

每类必须有 operation-specific precondition、identity semantics、execute attempts、replay policy、observer、partial boundary、convergence identity 和 recovery policy。不得因使用同一 Runtime 而共享不适用的 Page Reparent 禁止重放规则或 Copy/Move partial 语义。

### 专项 Lifecycle Strategy

- `open_hierarchy(create_type=none)`：typed active identity 与 bounded convergence；
- `close_notebook`：confirmation、execute-once 与 open-state 收敛；
- `sync_notebook`：只表达 `accepted=true/completion_unobservable`，不得伪造完成证明；
- 未来显式启动 OneNote GUI 的能力继续由 [TODO 031](031_start_onenote_desktop_tool.md) 单独治理权限与 UI 收敛。

### Read Strategy

- Get/List/Expand/Query/Search 等只读 Tool 使用 shared lease、budget、cache policy、typed error 与统一 Outcome；
- 不引入 mutation reconciliation；
- live confirmation/postcondition 必须独立于任何可选 cache，保持 live backend 读取；原 TODO 024 的 TTL cache 方案已在后续取消。

### Filesystem 与 UI Effect Strategy

- Publish 使用精确路径、overwrite policy、执行后文件结果和 filesystem-specific recovery；
- Navigate 只表达 action accepted，不声明 OneNote 持久状态已改变；
- 不能为了统一而让 filesystem/UI 获得 OneNote mutation 权限，或让 Runtime 成为敏感内容汇聚点。

### 允许的例外

- 纯进程内 health/config projection、注册自检或不接触 backend 的静态 Plan 可以使用精简策略；
- 任何公开 Tool 若暂不经过 Runtime，必须在 Registry 审计中有具名、可复核的理由，不得静默绕过；
- Bridge、Service 和测试 helper 可以作为内部依赖存在，但公开 Tool adapter 不得直接调用 Bridge。

## 分阶段实施

### 阶段 A：Canonical 设计与 inventory

- [x] 在 `docs/design/` 新增或整合 Operation Runtime canonical 设计，明确层次、依赖、阶段、对象和各 Operation kind 的结果语义；
- [x] 盘点所有当前公开 Tool，记录 operation name、kind、backend、capability、coordination、budget、cache、side effect 和当前 Handler；
- [x] 明确 control plane/data plane 分界，禁止 Runtime audit 捕获正文、raw XML、binary、完整路径、secret 或原始参数；
- [x] 确认与当时规划中的 TODO 024、029、031、034、035 的依赖和非重叠范围；TODO 024 已在后续取消。

### 阶段 B：Runtime 骨架与兼容入口

- [x] 实现 `OperationSpec`、`OperationRegistry`、`OperationExecution`、`OperationOutcome` 和 `OperationRuntime`；
- [x] 将现有 `tools.responses.invoke()` 适配到 Runtime，保持公开 Tool 参数和既有 envelope 兼容并增加稳定 `execution` 字段；
- [x] 将 coordinator、typed error mapping、deadline/budget hook、cache generation 和 content-free finalize 收入固定控制协议；
- [x] 保证异常、取消、timeout 和 Handler bug 的所有出口都释放 lease；
- [x] 增加启动时 Registry 完整性检查和测试，拒绝公开 Tool 缺少 Spec/Strategy/Handler。

### 阶段 C：Read 与简单 effect 迁移

- [x] 迁移全部 Get/List/Query/Expand/Search，并验证 shared lease、预算与未来 cache policy；
- [x] 迁移 `sync_notebook`，保留 accepted-not-completed 语义；
- [x] 迁移 Publish/Navigate 时使用各自 Outcome，不引入伪 postcondition；
- [x] 审计并消除公开 Tool adapter 对 Service/Bridge 的非 Runtime 调用。

### 阶段 D：Mutation Strategy 推广

- [x] 复用 TODO 029 已验证的 `MutationAttemptPolicy/Executor/Outcome` 与四态原语，不维护第二套 attempt 状态模型；
- [x] 由 canonical Registry 唯一登记取代 `MUTATION_ATTEMPT_POLICY_BINDINGS`，避免双重权威来源；
- [x] 迁移 Rename、Page title、Delete、Close 等边界较清晰的 operation；
- [x] 迁移 Create、Reorder 和三类 Reparent；
- [x] 迁移 Copy/Move，并保留内部 planning、identity map、Saga completed steps、Copy gate 与 source-delete 负合同；本轮不交付 Preview；
- [x] 形成 executable mutation policy catalog，并同步 canonical design/tool contracts。

### 阶段 E：默认 Tool 面闭环

- [x] 默认 profile 的每个公开 Tool 均经 Runtime，无未登记例外；
- [x] 开发/高级 profile 同样完成 inventory，不得因低层定位绕过已有 policy；
- [x] 删除已无用途的布尔 `mutation=True` 分散调用和重复 response/error glue；
- [x] 检查 Tool 面收敛结果与 [TODO 034](034_pre_user_testing_tool_surface_convergence.md) 当前基线一致；TODO 034 其余 exposure 决策保持独立待办。

## 自动化验证

至少覆盖：

- [x] Registry 中每个公开 Tool 恰好对应一个 OperationSpec、Strategy 和 Handler；
- [x] Read/Mutation/Lifecycle/Filesystem/UI 五类代表性 Tool 经过正确阶段和 coordination mode；
- [x] policy 拒绝发生在 backend execute 之前；
- [x] deadline、budget、coordination timeout、Handler exception、reconciliation failure、finalize failure 和 BaseException/取消出口均释放 lease；
- [x] mutation 前 generation invalidation 恰好发生一次，confirmation/postcondition 使用 live state；
- [x] Runtime 不记录或回传 Page 正文、raw XML、binary、完整路径、secret、bridge payload 或原始参数；
- [x] Sync 保持 accepted-not-completed；Navigate 不伪造持久化成功；Publish 使用 filesystem outcome；
- [x] TODO 029 的 execute-once、四态 reconciliation、reconciled success、negative lifecycle contract 不回退；
- [x] Copy/Move 的 ID map、fidelity、copy-only、source-delete 和 partial recovery 合同不回退；
- [x] 公开参数和现有成功/失败 envelope 保持兼容，additive `execution` 字段已在 design/README 记录；
- [x] 聚焦测试与完整 `.venv\Scripts\python.exe -m pytest -q` 通过。

## Human-gated 真实验证

Runtime 骨架本身不得创造新的真实 mutation。每个迁移阶段如果改变非只读 Tool 的实际执行链，必须继续使用 `tests/manual_validation/` 下已有具名 scenario；缺少场景的 mutation 必须先补具名隔离 scenario。Agent 只能运行纯测试和 `--dry-run`，绝不能执行真实 `run.py <scenario>` 或 `run.py all`。

用户确认范围应与实际迁移影响相称：

- Read Strategy 使用受影响的 Query/Hierarchy/Search fresh/cache 场景；
- Mutation Strategy 至少覆盖受影响的 Create、Reorder/Reparent、Delete/Close 与 Copy/Move 代表路径；
- TODO 029 完成后的 `reparent-page` fresh/cache 证据仍按其完成定义独立记录；
- Lifecycle、Filesystem 或 UI Strategy 若改变真实副作用行为，必须有对应具名场景和最小权限证据；
- 任何真实证据未由用户确认前，不得以 mock、dry-run 或 Agent 推断标记对应阶段完成。

## 非目标

- 不把所有 Tool 建模为 mutation；
- 不让 Runtime 解释 OneNote Page XML、正文、binary 或 Copy fidelity；
- 不建立跨 MCP 进程事务、daemon、后台 watcher 或 OneNote 全局锁；
- 不通过 Runtime 自动启动、关闭、重开或 Sync 用户 Notebook 来建立 readiness；
- 不改变 local-only 边界，不引入 Graph、Azure、OAuth、遥测或远程内容处理；
- 不直接读取、解析或修改 `.one`/`.onetoc2`；
- 不把大规模全 Tool 迁移作为 TODO 029 修复 Reparent 的前置条件；
- 不仅为了统一接口而保留 TODO 034 认定应隐藏或删除的 Tool；
- 不在本 TODO 中替代 TODO 035 对 Copy/Move 公开 Planning/Preview 与 Agent 职责的产品决策。

## 风险与约束

- Runtime 可能演化为 God Object；必须只拥有控制面，业务判断留在 Handler/Service；
- 过度统一可能把 Sync accepted、Navigate action 或 filesystem success 错写成 mutation applied；必须维持 Strategy-specific outcome；
- 全 Tool 迁移影响面很大，应保持兼容 adapter 并分批提交、验证，禁止一次性机械重写；
- Registry 可能成为新的静态重复源；实现后 canonical 行为应落入 design/tool contracts，并通过测试校验 Registry 与公开 Tool 面一致；
- 统一审计容易聚合敏感数据；所有 safe diagnostics 必须使用 allowlist projection；
- 当前工作树和后续实现可能存在无关变更，迁移不得覆盖用户改动或使用破坏性 Git 操作。

## 完成定义

- [x] `docs/design/` 中存在已审查的 Operation Runtime canonical 架构，TODO 不成为唯一权威来源；
- [x] `OperationRuntime`、Registry、Spec、Execution、Outcome 与分类型 Strategy 已实现并具有清晰依赖方向；
- [x] 默认 profile 的公开 Tool 全部经过 Runtime，无未登记例外；
- [x] Mutation Strategy 复用 TODO 029 的状态模型，全部 mutation 具有 operation-specific attempts/replay/identity/observer/partial/recovery policy；
- [x] Read、Lifecycle、Filesystem 和 UI 不被错误地套用 mutation 完成语义；
- [x] 公开参数保持兼容，响应变化为已记录的稳定 additive contract；
- [x] 聚焦测试和完整 pytest 通过；
- [x] 所有受影响非只读执行链均具有自动化合同、具名 manual scenario，并由用户确认所要求的真实回归证据；
- [x] README、architecture、tool contracts、开发/验证文档和 TODO 索引同步；
- [x] TODO 029、034、035 的独立完成定义未被本 TODO 稀释或替代。

## 当前实施证据（2026-08-15）

- [`operation_runtime.md`](../design/operation_runtime.md) 是当前 canonical 控制面契约；`operation_catalog.py` 精确登记唯一生产 profile 56 项，advanced profile 与 Registry binding 均为 0。
- 公开 adapter 只调用 `responses.invoke(operation, arguments)`；源码合同拒绝 `get_services`、`mutation=True` 和 adapter `.bridge` 旁路。
- Registry/Runtime 覆盖 Read、Mutation、Lifecycle、Filesystem Effect、UI Effect；成功路径会把 029 嵌套 reconciliation 组合为 operation attempts/replay/outcome，Sync/Navigate/Publish 保持各自完成语义。
- `onenote-convergence` 已扩展为具名 Lifecycle/Filesystem/UI 真实场景，并补齐公开 Notebook Create+Close、Replace Body、URL Navigate，继续覆盖 mutation 与 production source Close；Agent 只运行其纯合同与 dry-run，不运行真实场景。
- Runtime/Server/Copying/container reorder/mutation-control 聚焦测试通过 227 项；manual-validation 纯合同通过 574 项；完整 pytest 通过 1022 项。跨 production/manual catalog 的覆盖断言已上移到顶层 Runtime 测试，故不计入 manual-validation 自身测试数；新增一项纯合同证明 accepted-but-not-complete 不会被共享 client 误判。
- 自动化映射证明 31 个非只读 production Operation 全部至少进入一个具名 manual scenario allowlist；`onenote-convergence` 的纯执行合同进一步精确断言新增 Notebook Create+Close、Replace Body 与 URL Navigate 的真实调用顺序和 Runtime outcome。
- Manual-validation 保持 transport 黑盒：其核心和测试不导入 Runtime/Registry/catalog/context/server 或 mutation-control；跨 catalog 覆盖断言归属顶层 Runtime 测试，场景只消费公开 content-free `execution` 字段。AST 源码负合同防止控制面依赖回渗。
- `run.py all --dry-run` 与 `run.py all --use-cache --dry-run` 的 18 个批处理场景均全部通过，`onenote-convergence --dry-run --json` 也通过并投影最小 effect allowlist；这些结果不替代用户真实 OneNote 证据。
- 七个 Copy/Move 与 Query/Search/Hierarchy/Reparent Page 共 11 条独立 cache dry-run 均通过，未访问 cache 或 OneNote。
- 用户已确认要求范围内的其他真实场景全部通过；最终 `run-2026-08-15-18-20-50` 也完整通过 `onenote-convergence`。Durable 证据记录 `status=passed`、`restored=true`、单 scenario MCP、48 次 Tool 调用和 source `closed_preserved`；Sync 为 accepted-completion-unobservable，Publish/两种 Navigate 分别证明 Filesystem/UI outcome，Notebook Create+Close、Page Title/Append/Content Delete/Reorder/Delete/production Close 均为 applied、单次 attempt、无 replay，Replace 非原子 saga 为 applied、一次 execute、非 partial 并替换一个 fixture body object。生产 Close 的 exact handoff 被 lifecycle wrapper 单次采用，没有二次 Close；工作文件和证据按合同保留。
- 前三次 `onenote-convergence` 失败均由验收端合同偏移触发：前两次是共享 stdio client 把合法 `complete=false` Sync 响应误判为失败，第三次是叶子场景把不属于 029 principal-attempt inventory 的 Replace saga 当成普通 attempt。两处修复均保持 manual-validation 为 transport 黑盒，没有修改生产 Runtime 语义或向手动框架核心引入控制面依赖；每次 failure finalizer 都精确关闭 Notebook，没有不确定 mutation。
- 至此阶段 A–E、自动化合同、manual dry-run、真实 Read/Mutation/Copy/Move/Lifecycle/Filesystem/UI 回归和文档同步均闭合，本 TODO 状态更新为“已完成”。

## 关联

- [Design：当前架构](../design/architecture.md)
- [Design：Operation Runtime](../design/operation_runtime.md)
- [Design：当前 Tool 合同](../design/tool_contracts.md)
- [TODO 024：Search 与 Typed Query 短时只读快照缓存（已取消）](024_search_and_query_read_snapshot_cache.md)
- [TODO 025：OneNote COM 收敛、Mutation 对账与调用协调](025_onenote_com_convergence_and_mutation_coordination.md)
- [TODO 029：Mutation Readiness 与 Page Reparent 加固](029_mcp_mutation_readiness_and_reconciliation_hardening.md)
- [TODO 031：显式 launch_onenote_gui 工具](031_start_onenote_desktop_tool.md)
- [TODO 034：用户测试前 MCP 工具面收敛](034_pre_user_testing_tool_surface_convergence.md)
- [TODO 035：Copy/Move Planning 与 Agent 职责收敛](035_copy_move_internal_planning_and_agent_role.md)
