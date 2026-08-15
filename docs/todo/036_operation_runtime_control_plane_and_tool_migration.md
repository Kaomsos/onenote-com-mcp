# 036：Operation Runtime 操作执行控制层与 Tool 迁移

> ID：036
> 状态：待办
> 优先级：P1
> 类型：生产架构 / Tool 执行控制面 / 一致性与可观测性
> 更新日期：2026-08-15

## 决策摘要

将当前分散在 Tool adapter、`tools.responses.invoke()`、进程内读写协调器、各 Service、公共 convergence/reconciliation helper 和 response mapper 中的跨切面执行规则，提升为 transport-independent 的 **Operation Runtime（操作执行控制层）**。大部分公开 MCP Tool 应通过同一个 Runtime 完成准入、授权、协调、deadline/预算、cache 一致性、执行策略、错误标准化、安全审计和结果收敛，再由 operation-specific Handler 保留实际 OneNote、filesystem 或 UI 业务语义。

本 TODO 推广统一执行模型，但不把所有 Tool 强行建模为 mutation。Runtime 下至少区分 Read、Mutation、Lifecycle、Filesystem Effect 与 UI Effect；只有真正改变 OneNote 持久状态的操作使用 `logical_ready → execute according to operation policy → reconcile actual outcome → converge → validate postcondition` 的完整 mutation 状态机。

本 TODO 不吸收或替代 [TODO 029](029_mcp_mutation_readiness_and_reconciliation_hardening.md)。TODO 029 继续以 `reparent_page` 完成 execute-once、四态 reconciliation、reconciled success、恢复建议和生命周期负合同；其结果应成为 `MutationExecutionStrategy` 的首个完整纵向切片。Operation Runtime 的全 Tool 推广不得成为 TODO 029 闭环的前置阻塞。

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
| `MutationContract` / `MutationOutcome` | `OperationSpec`/`OperationOutcome` 的 mutation 专项扩展，承载 logical preflight、identity、attempt/replay、四态对账和 postcondition。 |

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
- live confirmation/postcondition 必须能声明绕过 [TODO 024](024_search_and_query_read_snapshot_cache.md) 规划的 TTL cache。

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

- [ ] 在 `docs/design/` 新增或整合 Operation Runtime canonical 设计，明确层次、依赖、阶段、对象和各 Operation kind 的结果语义；
- [ ] 盘点所有当前公开 Tool，记录 operation name、kind、backend、capability、coordination、budget、cache、side effect 和当前 Handler；
- [ ] 明确 control plane/data plane 分界，禁止 Runtime audit 捕获正文、raw XML、binary、完整路径、secret 或原始参数；
- [ ] 确认与 TODO 024、029、031、034、035 的依赖和非重叠范围。

### 阶段 B：Runtime 骨架与兼容入口

- [ ] 实现 `OperationSpec`、`OperationRegistry`、`OperationExecution`、`OperationOutcome` 和 `OperationRuntime`；
- [ ] 将现有 `tools.responses.invoke()` 适配到 Runtime，先保持公开 Tool 参数和 envelope 兼容；
- [ ] 将 coordinator、typed error mapping、deadline/budget hook、cache generation 和 content-free finalize 收入固定控制协议；
- [ ] 保证异常、取消、timeout 和 Handler bug 的所有出口都释放 lease；
- [ ] 增加启动时 registry 完整性检查和测试，拒绝公开 Tool 缺少 Spec/Strategy/Handler。

### 阶段 C：Read 与简单 effect 迁移

- [ ] 先迁移代表性 Get/List/Query/Expand/Search，验证 shared lease、预算与未来 cache policy；
- [ ] 迁移 `sync_notebook`，保留 accepted-not-completed 语义；
- [ ] 迁移 Publish/Navigate 时使用各自 Outcome，不引入伪 postcondition；
- [ ] 审计并消除公开 Tool adapter 对 Service/Bridge 的非 Runtime 调用。

### 阶段 D：Mutation Strategy 推广

- [ ] 复用 TODO 029 已验证的 MutationContract/Outcome 或等价原语，不维护第二套状态模型；
- [ ] 先迁移 Rename、Page title、Delete、Close 等边界较清晰的 operation；
- [ ] 再迁移 Create、Reorder 和三类 Reparent；
- [ ] 最后迁移 Copy/Move，并保留其 plan/preview 决策、identity map、Saga completed steps、Copy gate 与 source-delete 负合同；
- [ ] 形成 executable mutation policy catalog，并同步 canonical design/tool contracts。

### 阶段 E：默认 Tool 面闭环

- [ ] 默认 profile 的每个公开 Tool 均经 Runtime，或存在文档化、自动审计的具名例外；
- [ ] 开发/高级 profile 同样完成 inventory，不得因低层定位绕过已有 policy；
- [ ] 删除已无用途的布尔 `mutation=True` 分散调用和重复 response/error glue；
- [ ] 检查 Tool 面收敛结果与 [TODO 034](034_pre_user_testing_tool_surface_convergence.md) 一致，不能为了 Registry 完整而保留不应公开的 Tool。

## 自动化验证

至少覆盖：

- [ ] Registry 中每个公开 Tool 恰好对应一个 OperationSpec、Strategy 和 Handler；
- [ ] Read/Mutation/Lifecycle/Filesystem/UI 五类代表性 Tool 经过正确阶段和 coordination mode；
- [ ] policy 拒绝发生在 backend execute 之前；
- [ ] deadline、budget、coordination timeout、Handler exception、reconciliation failure 和 finalize failure 均释放 lease；
- [ ] mutation 前 generation invalidation 恰好发生一次，confirmation/postcondition 使用 live state；
- [ ] Runtime 不记录或回传 Page 正文、raw XML、binary、完整路径、secret、bridge payload 或原始参数；
- [ ] Sync 保持 accepted-not-completed；Navigate 不伪造持久化成功；Publish 使用 filesystem outcome；
- [ ] TODO 029 的 execute-once、四态 reconciliation、reconciled success、negative lifecycle contract 不回退；
- [ ] Copy/Move 的 ID map、fidelity、copy-only、source-delete 和 partial recovery 合同不回退；
- [ ] 公开参数和现有成功/失败 envelope 保持兼容，任何 additive 字段均有 design/README 记录；
- [ ] 聚焦测试与完整 `.venv\Scripts\python.exe -m pytest -q` 通过。

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

- `docs/design/` 中存在已审查的 Operation Runtime canonical 架构，TODO 不成为唯一权威来源；
- `OperationRuntime`、Registry、Spec、Execution、Outcome 与分类型 Strategy 已实现并具有清晰依赖方向；
- 默认 profile 的公开 Tool 全部经过 Runtime，或有少量具名、自动审计且有充分理由的例外；
- Mutation Strategy 复用 TODO 029 的状态模型，全部 mutation 具有 operation-specific attempts/replay/identity/observer/partial/recovery policy；
- Read、Lifecycle、Filesystem 和 UI 不被错误地套用 mutation 完成语义；
- 公开参数保持兼容，响应变化为已记录的稳定 additive contract；
- 聚焦测试和完整 pytest 通过；
- 所有受影响非只读执行链均具有自动化合同、具名 manual scenario，并由用户确认所要求的真实回归证据；
- README、architecture、tool contracts、开发/验证文档和 TODO 索引同步；
- TODO 029、034、035 的独立完成定义未被本 TODO 稀释或替代。

## 关联

- [Design：当前架构](../design/architecture.md)
- [Design：当前 Tool 合同](../design/tool_contracts.md)
- [TODO 024：Search 与 Typed Query 短时只读快照缓存](024_search_and_query_read_snapshot_cache.md)
- [TODO 025：OneNote COM 收敛、Mutation 对账与调用协调](025_onenote_com_convergence_and_mutation_coordination.md)
- [TODO 029：Mutation Readiness 与 Page Reparent 加固](029_mcp_mutation_readiness_and_reconciliation_hardening.md)
- [TODO 031：启动 OneNote Desktop GUI 的显式工具](031_start_onenote_desktop_tool.md)
- [TODO 034：用户测试前 MCP 工具面收敛](034_pre_user_testing_tool_surface_convergence.md)
- [TODO 035：Copy/Move Planning 与 Agent 职责收敛](035_copy_move_internal_planning_and_agent_role.md)
