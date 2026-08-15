# 029：MCP Tool Mutation Readiness 状态建模与 Page Reparent 加固

> ID：029
> 状态：已完成
> 优先级：P1
> 类型：生产可靠性 / MCP Tool 生态 / Mutation 安全
> 更新日期：2026-08-15

## 目标

把 [`mutation_readiness_and_call_design.md`](../design/mutation_readiness_and_call_design.md) 的平台限制落实到生产 MCP tool：不再试图用 `SyncHierarchy`、稳定 snapshot、sleep 或 filesystem 状态伪造 `mutation_ready`，而是明确 `logical_ready → execute once → reconcile actual outcome` 的调用合同。

第一阶段以 `reparent_page` 为完整纵向切片，解决本次 Page Reparent 暴露的生产风险；第二阶段抽取一个有界 principal backend attempt 的 policy/executor/outcome 原语，形成 MCP 生态一致但 operation-specific 的矩阵。Create、Copy/Move、`replace_page_body` 等多阶段 workflow、operation-wide saga、统一 Registry 和全 Tool Runtime 明确由 [TODO 036](036_operation_runtime_control_plane_and_tool_migration.md) 承接，不属于 029 的完成门。

## 已有基础与缺口

已有基础：

- typed ID、confirmation、同 Notebook 与 scope 验证；
- 进程内 mutation 独占协调；
- Reparent 两次稳定 hierarchy + 完整 evidence/bookend read-back；
- typed HRESULT 与最内层 COM HRESULT、wrapper HRESULT 诊断；
- 生产 Reparent 不依赖 `SyncHierarchy`，也不会自动 close/reopen；
- 当前实现后的 disposable `reparent-page` fresh 与 validated-cache-hit 均已有用户真实成功证据；Runner 已移除旧归因产生的 persistence checkpoint。

本轮已补齐：

- `reparent_page` 主 execute 异常后的 `applied/not_applied/partially_applied/indeterminate` 完整对账；
- execute 报错但完整 postcondition 成立时的 reconciled success；
- 稳定、content-free、Agent 可行动的阶段、重试安全和恢复建议字段；
- 主 Reparent mutation 只调用一次，且生产路径绝不调用 Sync/Close/Open/filesystem readiness probe 的负合同；
- 其他 bounded mutation attempts 的 readiness/replay policy 生态审计；
- 当前生产 policy 全部收敛为 execute-once，避免 digest 或局部状态被误当作完整 exact pre-state；
- 将 `SingleStage*` 更名为 `MutationAttempt*`，明确 root-only Reparent 的 descendant promotion 不属于 principal attempt 计数。

029 已完成自动化回归、固定 Section/SectionGroup 双 case Rename、扩展 `onenote-convergence` 与 production Close→pre-closed lease handoff 的用户真实验证。多阶段 mutation saga 不属于 029，继续由 036 承接。

## 阶段 A：`reparent_page` 生产加固

### Logical preflight

- [x] 冻结 live typed Page、source/destination Section、同 Notebook、活动态、confirmation、scope、内容基线、预算和 hierarchy bookend；
- [x] 内部状态只命名为 `logical_ready`，不公开或推导 `mutation_ready=true`；
- [x] preflight 失败统一记录 `mutation_stage=preflight`、`mutation_attempted=false`、`mutation_replayed=false`；
- [x] confirmation/read-back 继续使用 live 状态，并为未来 TTL read cache 保留绕过约束。

### Execute-once 与 reconciliation

- [x] 主 Reparent `UpdateHierarchy` 调用次数固定为 1，任何 HRESULT 均不自动重放；
- [x] execute 返回成功或异常后都用同一个 operation-specific observer 读取实际状态；
- [x] 完整 destination、唯一 ID map、scope、内容和无关对象 postcondition 成立时分类 `applied`；
- [x] execute 抛错但 `applied` 时按成功返回，并增加 `execute_error_reconciled=true`、`mutation_attempts=1`、`mutation_replayed=false`；
- [x] 只有完整 frozen pre-state、无 fresh/removed/remapped Page、无 destination candidate、无 promotion/其他变化时分类 `not_applied`；
- [x] descendant promotion 已发生、出现部分 topology/identity/content 变化或完整 postcondition 不成立时分类 `partially_applied`；
- [x] 读取失败、目标歧义、bookend 震荡或证据不足时分类 `indeterminate`；
- [x] reconciliation 最多重试一次只读取证，绝不重放 mutation。

### 错误与恢复合同

- [x] 失败 details 稳定包含 `mutation_stage`、`mutation_attempted`、`mutation_attempts`、`mutation_replayed`、`observed_outcome`、`preflight_state`、`persistence_checkpoint`、`retry_safety` 和 `recommended_action`；
- [x] `not_applied` 且最内层 HRESULT 为 not-yet-synchronized/file unavailable 时，可建议用户在 OneNote 中显式关闭并重开后发起新调用；
- [x] modal UI 只建议关闭对话框；确定性非法请求要求修正输入；
- [x] `partially_applied` 和 `indeterminate` 明确禁止重放，并要求使用只读 Tool 查询 current ID/位置或人工恢复；
- [x] 不根据 wrapper `0x80131501`、错误消息字符串或固定等待推断 retry safety；
- [x] response/audit 不包含 Page 正文、raw XML、binary、完整路径、secret 或原始参数。

### Lifecycle 与权限负合同

- [x] 生产 `reparent_page` 不调用 `sync_hierarchy`、`close_notebook`、`open_hierarchy` 或 filesystem Notebook readiness probe；
- [x] 不新增 lifecycle、Delete、Copy、Move、Raw XML 权限，不修改现有公开参数；
- [x] 不把 manual-validation 的 disposable lifecycle 迁入生产业务 tool；
- [x] 若未来需要公开 `checkpoint_notebook`，另立设计/TODO并进行独立权限、confirmation、UI/ID remap 审查，本 TODO 不实现。

## 阶段 B：MCP mutation tool 生态审计

- [x] 为 Page title/content mutation、Rename、受支持 Reorder、三类 Reparent、typed Delete 和 Close 建立 bounded-attempt readiness/replay policy 矩阵；
- [x] 每个纳入的 operation 明确 identity、execute attempts、reconciliation observer、partial boundary 和是否允许 replay；
- [x] attempt control 没有显式 `MutationAttemptPolicy` 时 fail closed；
- [x] `SyncHierarchy`、OpenHierarchy object ID、单次 snapshot 或 COM success 不得被任何 tool 文档写成完成证明；
- [x] 已由 TODO 025 完成且合同充分的工具不机械重写，只补统一 contract/outcome 与负合同；
- [x] 将 bounded-attempt 矩阵同步回 design/tool contracts，TODO 不成为唯一权威来源；
- [x] 明确将 Create、Copy/Move、`replace_page_body` 等多阶段 mutation 的 saga/checkpoint/compensation，以及 operation-wide Registry/Runtime 移交 TODO 036，不在 029 维护第二套架构；
- [x] 用 `MUTATION_ATTEMPT_POLICY_BINDINGS` 固化当前公开 Tool→attempt policy inventory，并以自动化测试校验 key、policy 引用和所有生产 policy 的 execute-once 约束。

## 自动化验证

至少覆盖：

- [x] 最内层 HRESULT 穿透 wrapper，分类不受 wrapper `0x80131501` 覆盖；
- [x] execute 异常 + 精确 pre-state → `not_applied`；
- [x] execute 异常 + 完整 postcondition → reconciled success；
- [x] Page 已移动但内容或无关对象 invariant 失败 → `partially_applied`；
- [x] descendant promotion 后主 Reparent 失败 → partial，不能判为整体 `not_applied`；
- [x] reconciliation 读取失败、候选不唯一或 hierarchy 震荡 → `indeterminate`；
- [x] 所有路径主 Reparent mutation 调用次数恒为 1；
- [x] 不调用 Sync/Close/Open/filesystem probe；
- [x] 所有失败有非空阶段、outcome、retry safety，且 audit/response content-free；
- [x] 现有成功、ID remap、root-only/subtree、restore 和两阶段 read-back 合同不回退；
- [x] `delete_page_content` 冻结完整对象 ID 集；非目标对象漂移必须分类 partial 且不能重放；
- [x] response mapper 对 `MutationPreflightFailure`/`MutationFailure` 保留稳定 code/details；
- [x] 本次收尾后的聚焦纯测试与完整 `.venv\Scripts\python.exe -m pytest -q` 通过。

2026-08-15 自动化证据：mutation/Reparent/manual-contract 聚焦回归 `56 passed`；最终完整纯测试 `990 passed in 67.88s`；`reparent-page` fresh 与 `--use-cache` 两条 `--dry-run --json` 均通过，且明确报告 `agent_execution_prohibited=true`、`server_started=false`。未执行真实 OneNote scenario。

2026-08-15 首轮收尾证据：manual-validation 纯测试 `577 passed`，完整纯测试 `998 passed in 66.19s`；当时的两条 dry-run 均报告 `agent_execution_prohibited=true`、`server_started=false`。随后真实运行暴露 convergence 场景误用了内部 Page object 字段，以及参数化 Rename fixture 的非必要复杂度；两项均在下文继续收敛，首轮计数不作为最终版本完成证据。

2026-08-15 第二轮收敛证据：相关场景/registry/cache/failure-handoff 聚焦测试 `259 passed`，manual-validation 全集 `576 passed`，完整纯测试 `997 passed in 66.61s`。测试总数减少 1 是因为删除 Rename 的 target 变体后只保留一个 canonical 注册 dry-run case；固定双 case 场景本身新增了正向、逆序恢复、异常先恢复和 keep-worksite 覆盖。最终 `onenote-convergence --dry-run --json` 与 `rename --use-cache --dry-run --json` 均通过并报告 `agent_execution_prohibited=true`、`server_started=false`。

2026-08-15 production-close handoff 收敛证据：lifecycle/convergence/统一收尾/注册 dry-run 聚焦测试 `186 passed`；最终完整纯测试 `1000 passed in 65.53s`。`onenote-convergence --dry-run --json` 通过，报告 `agent_execution_prohibited=true`、`server_started=false`，并在 lifecycle plan 中显式列出 `adopt_production_close`。未执行真实 OneNote scenario。

## Human-gated 真实验证

生产实现完成后，继续使用现有具名 `reparent-page` 场景验证正常成功路径；fixture checkpoint 只构造可靠 disposable 输入，production bridge audit 必须证明业务 tool 未调用 Sync/Close/Open。用户应分别运行 fresh 与 `--use-cache`，确认正向、ID remap、内容/无关对象、恢复和 lifecycle 全部通过。

错误分支不得为了验收而人为破坏真实 OneNote 或制造 modal dialog。`not_applied/partial/indeterminate` 主要由 deterministic fake timeline 覆盖；若用户自然遇到相应真实 HRESULT，可补充脱敏 evidence，但不是通过危险操作制造失败的前置条件。Agent 绝不能执行真实 scenario。

### 2026-08-15 当前实现证据

- `run-2026-08-15-12-36-04`：用户运行 fresh `reparent-page`，正向与恢复均为 `applied`，每次 `mutation_attempts=1`、`mutation_replayed=false`；bridge audit 分别证明恰好一次 `update_hierarchy` 且无 Sync/Close/Open，Page/内容对象 ID remap、富内容、无关对象、目标位置、恢复和最终 `closed_preserved` 全部通过。
- `run-2026-08-15-12-37-08`：用户运行 `reparent-page --use-cache`，`cache.decision=validated_hit`；与 fresh 相同的 reconciliation、bridge 负合同、ID remap、内容、恢复和 lifecycle 全部通过。至此本 TODO 要求的当前版本 Page Reparent fresh/cache 人工门已闭合。
- `run-2026-08-15-12-40-55`：`reparent-page-with-level --use-cache` 通过；root-only descendant promotion 与 full-subtree 两条路线均为 `applied`、单次主 Reparent、无 replay，并各有两次稳定观察。
- `run-2026-08-15-12-41-43`：`reparent-section --use-cache` 的三种父级迁移与逆序恢复通过，`validated_hit/closed_preserved`；抽查 response 为 `applied`、一次执行、无 replay、两次稳定观察。
- `run-2026-08-15-12-42-45`：`reparent-section-group --use-cache` 的三种父级迁移与逆序恢复通过，`validated_hit/closed_preserved`；抽查 response 同样为 `applied`、一次执行、无 replay、两次稳定观察。
- `run-2026-08-15-12-43-58`：fresh-only `onenote-convergence` 通过并恢复；Append、Page Reorder、typed Page Delete 均返回 `applied`、一次执行、无 replay、两次稳定观察，最终 lifecycle 为 `closed_preserved`。
- `run-2026-08-15-12-44-29`：默认 Section `rename --use-cache` 正向与恢复通过，`validated_hit/closed_preserved`。
- `run-2026-08-15-12-45-01`：`reorder-section --use-cache` 的 Notebook/SectionGroup 两种 parent case 与恢复通过，`validated_hit/closed_preserved`。
- `run-2026-08-15-12-44-46` **不是成功证据**：`rename --target group_a --use-cache` 在 mutation 前因 materialized manifest 缺少 `structure.group_a` fail closed；审计仅有只读 Health/Expand 调用，没有 Rename mutation，failure finalizer 已精确关闭 Notebook，cache 未修改。需先修复 Rename cache variant 将 CLI target 纳入 recipe/materialization 合同，再补 SectionGroup Rename 证据。
- `run-2026-08-15-13-26-00` **不是成功证据**：扩展 `onenote-convergence` 的 Title 与 Append 已通过，但场景把公开 PageContentObject 字段 `id/can_delete/delete_target_id` 错写成内部 parser 字段，因而在内容删除前误判候选并 fail closed；未调用 `delete_page_content`，failure finalizer 已精确关闭 Notebook。该问题不属于生产 mutation 失败。
- `run-2026-08-15-13-26-25`：参数化 `rename --target group_a --use-cache` cold build 后通过，SectionGroup Rename 正向、恢复与 `closed_preserved` 均成功。这份证据证明生产 `rename_section_group` 路径，但参数化场景已由固定双 case 设计取代，仍需在最终 canonical 场景上补一次真实运行。
- `run-2026-08-15-13-45-33` **不是成功证据**：扩展 convergence 已到达内容删除，生产 Bridge 删除成功且未重放，但 observer 当时只允许目标 Outline ID 消失，把同属删除容器的 OE 后代正常消失误判为非目标漂移；运行 fail closed 并精确关闭。生产 reconciliation 随后改为冻结目标完整后代闭包，并补充嵌套 Image 合同测试。
- `run-2026-08-15-13-46-17`：最终 canonical `rename --use-cache` 通过，固定 Section→SectionGroup 两个正向 case均完成、逆序恢复成功，最终 `validated_hit/closed_preserved`。Rename 人工门至此闭合。
- `run-2026-08-15-13-58-47` **是生产 mutation 链成功证据，但不是完整 run 成功证据**：Title、Append、内容删除、Page Reorder、非永久 Page Delete 和生产 Close 均为 `applied`、`mutation_attempts=1`、`mutation_replayed=false`，各有两次稳定观察，fixture 也已恢复；但生产 Close 已关闭 Notebook 后，仍为 active 的共享 lease 又尝试二次 Close，最终状态为 `failure_finalization_failed`。因此当时仍需用户在 strict production-close→lifecycle pre-closed lease handoff 修复后复验；该 handoff 只接受 exact ID/name、双稳定、单次执行且未重放的完整证据，不二次调用 Close。
- `run-2026-08-15-14-16-16`：用户复验后的完整 `onenote-convergence` run 为 `passed`。Title、Append、内容删除、Page Reorder、非永久 Page Delete 和生产 Close 全部 `applied`、单次执行、无重放并有双稳定观察；fixture 恢复成功。Production Close evidence 以 `close_origin=production_close_notebook` 精确封存 active lease，最终 lease=`closed`、lifecycle=`closed_preserved`；bridge audit 为 scenario Close 1 次、lifecycle Close/Sync 0 次，证明 handoff 未二次执行 Close。至此 convergence 人工门闭合。

当前实现已把 `update_page_title`、`delete_page_content` 和生产 MCP `close_notebook` 加入 fresh-only `onenote-convergence` 的单一受控链，并把 production close 设为最后一个 scenario MCP mutation；共享 lifecycle 只在完整 exact production-close evidence 成立时把 active lease 封存为 pre-closed，不二次 Close。Convergence 场景只消费公开 PageContentObject schema。Rename recipe v3 只构建 `Rename-Group/Rename-Section`，单次 canonical 场景固定执行 Section→SectionGroup 两个正向 case并逆序恢复，不再暴露 `--target` 或 target-dependent cache manifest。

最终 human-gated 命令（已由用户显式执行并以上述 durable evidence 确认）：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py onenote-convergence
```

Agent 未执行上述真实命令。用户确认与 durable evidence 已同时闭合 Rename、convergence 和 lifecycle 收尾门限。

## 非目标

- 不建立不存在的只读 `mutation_ready` probe；
- 不自动关闭或重开用户 Notebook；
- 不读取、解析或修改 `.one`/`.onetoc2`；
- 不使用固定 sleep、全局放宽 timeout 或文件属性猜测持久化完成；
- 不无条件重放 UpdateHierarchy、Copy/Move/Create 或 source delete；
- 不建立跨进程 daemon、后台 watcher、云同步或 Microsoft Graph 依赖；
- 不改变 local-only、安全门限或现有最小权限。

## 完成定义

- 阶段 A 的四态 reconciliation、reconciled success、结构化恢复建议、单次 execute 与生命周期负合同全部实现；
- 阶段 B 的 bounded-attempt policy/executor/outcome、可执行 inventory 和全部生产 attempt execute-once 约束已实现；operation-wide 与多阶段 saga 由 TODO 036 承接；
- 公开参数保持兼容，新增字段为已记录的稳定 additive contract，文档与 README 同步；
- 聚焦测试和完整 pytest 通过；
- [x] 用户在实现后的当前版本显式确认 `reparent-page` fresh 与 cache 真实回归通过；
- [x] 用户确认固定双 case Rename 当前版本真实运行通过；
- [x] 用户确认扩展 `onenote-convergence` 的 mutation 链、production-close lifecycle handoff 与完整 run 当前版本真实运行通过；
- [x] 多阶段 mutation saga、operation-wide Registry 和全 Tool Runtime 已以明确边界交接 TODO 036，不作为 029 的完成门。

## 关联

- [Design：Mutation Readiness 状态模型](../design/mutation_readiness_and_call_design.md)
- [Lesson：Mutation Readiness 不能由 Preflight 证明](../lesson/onenote_mutation_readiness_is_not_preflight_observable.md)
- [TODO 025：公共 convergence/reconciliation/coordination 基础设施](025_onenote_com_convergence_and_mutation_coordination.md)
- [TODO 027：Reparent 人工验证矩阵与 checkpoint 真实证据](027_reparent_manual_validation_all_coverage.md)
- [TODO 036：Operation Runtime 操作执行控制层与 Tool 迁移](036_operation_runtime_control_plane_and_tool_migration.md)
