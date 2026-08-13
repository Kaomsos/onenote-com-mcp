# OneNote Mutation Readiness 状态模型与调用设计

> 状态：平台限制与状态模型为当前有效设计；生产 `reparent_page` 的完整执行异常对账加固尚未实施，由 [TODO 029](../todo/029_mcp_mutation_readiness_and_reconciliation_hardening.md) 跟踪
> 更新日期：2026-08-13
> 适用范围：Local OneNote MCP 的生产 mutation tools；manual-validation 的 disposable lifecycle 只作为受控特例

本文定义 OneNote COM mutation 的 readiness 边界、状态名称和调用顺序。公开参数、返回 envelope、HRESULT 与通用 reconciliation 仍以 [`tool_contracts.md`](tool_contracts.md) 为准，总体 service/coordination 架构以 [`architecture.md`](architecture.md) 为准。

## 1. 平台限制：执行前没有可证明的 `mutation_ready`

OneNote COM 没有提供只读的 `CanUpdateHierarchy`、flush-complete token 或等价接口。生产工具在调用 `UpdateHierarchy` 前，无法通过一个无副作用探针证明目标已经进入底层 mutation 可接受的持久化状态。

以下观察都不能单独证明 `mutation_ready`：

- `SyncHierarchy` 返回成功；它只证明请求被接受；
- 连续多次 `GetHierarchy` 得到相同 typed hierarchy；
- Page XML、正文或内容对象可以完整读取；
- `.one`/`.onetoc2` 文件存在、大小或 mtime 发生变化；
- 等待固定时长；
- `OpenHierarchy` 返回 object ID；
- 提交 no-op `UpdateHierarchy`；它本身就是 mutation，不能作为安全探针。

因此生产 tool 不得返回或内部推导虚假的 `mutation_ready=true`。正确问题不是“怎样提前确定一定成功”，而是“现有证据是否足以安全尝试一次，以及执行后怎样确定真实结果”。

## 2. 状态模型

| 状态 | 可证明内容 | 不可推导内容 |
| --- | --- | --- |
| `logical_ready` | 精确 typed ID、类型、活动态、parent、同 Notebook、confirmation、scope、内容基线与 hierarchy bookend 均通过 | 底层源文件已经提交；下一次 native mutation 一定成功 |
| `persistence_checkpointed` | 经显式 `CloseNotebook(false)`、exact-path reopen、ID rebind 和完整 live validation 后，关闭产生的持久化结构可重新使用 | 后续 mutation 必然成功；任意用户 Notebook 可被工具自动关闭 |
| `execute_attempted` | mutation 调用已经发出一次 | COM 返回异常等于未应用；COM 返回成功等于 postcondition 已成立 |
| `applied` | 完整 operation-specific postcondition 和 invariant 已由 live evidence 证明 | 后端调用过程没有抛错 |
| `not_applied` | 完整 frozen pre-state 保持不变，且没有 fresh/removed/remapped/partial 对象 | 可以无条件重试 |
| `partially_applied` | 已出现部分 topology、identity、内容或前置 mutation 变化，但完整 postcondition 不成立 | 重放整个操作是安全的 |
| `indeterminate` | 证据不足、读取失败、目标歧义或状态震荡，无法证明 pre/post/partial 中任一完整状态 | mutation 未发生 |

允许的抽象状态流为：

```text
logical_ready
  → execute once
  → applied | not_applied | partially_applied | indeterminate

disposable or explicitly authorized lifecycle only:
logical_ready
  → persistence_checkpointed
  → execute once
  → applied | not_applied | partially_applied | indeterminate
```

`persistence_checkpointed` 是比 `logical_ready` 更强的来源持久化证据，但仍不是绝对的 mutation-ready 保证。最终结果只能由 execute 后的 live reconciliation 与 invariant validation 确定。

## 3. 生产 mutation 的正确调用设计

生产 mutation tool 应按以下顺序工作：

1. **Logical preflight**：使用 live、非缓存状态确认精确 ID、类型、活动态、parent、同 Notebook、confirmation、scope、预算和完整保护基线；该阶段只能声明 `logical_ready`。
2. **Execute once**：调用一次业务 mutation。存在副作用或 identity remap 的操作默认不得把 timeout、同步错误或未知 HRESULT 当成重放许可。
3. **Reconcile actual state**：无论 execute 返回成功还是异常，都以 operation-specific observer 判断 `applied/not_applied/partially_applied/indeterminate`；异常不是“未应用”的证据。
4. **Converge and validate**：`applied` 仍须满足连续稳定 hierarchy、完整内容 evidence、bookend 和无关对象 invariant；读取瞬态错误只允许重读 evidence，不重放 mutation。
5. **Return actionable state**：成功与失败都只返回 content-free、Agent 可行动的结果；partial/indeterminate 明确禁止盲目重试。

### Page Reparent 专项约束

`reparent_page` 允许 Page 与内容对象发生一对一 ID remap，默认 root-only 路线还可能先执行 descendant promotion。因此：

- 主 Reparent `UpdateHierarchy` 只调用一次，`mutation_replayed=false`；
- 如果 descendant promotion 已完成而主 Reparent 失败，整体状态至少是 `partially_applied`，不能降级为 `not_applied`；
- execute 抛错但完整 destination、ID map、scope、内容和无关对象 postcondition 均成立时，可以按 `applied` 成功返回，并附 `execute_error_reconciled=true`；
- 只有完整 frozen pre-state、目标 Section 无 fresh candidate、无 ID remap、无 promotion 和无其他变化时，才能判为 `not_applied`；
- 目标候选不唯一、read-back 不完整或 bookend 不稳定时必须判为 `indeterminate`。

## 4. Lifecycle 与业务 mutation 的权限边界

生产 `reparent_page` 不得为了建立 readiness 而隐式调用：

- `SyncHierarchy` 作为完成证明；
- `CloseNotebook` 或 reopen；
- filesystem `.one` readiness probe；
- 固定 sleep；
- no-op mutation。

自动关闭用户 Notebook 会影响 UI、触发同步和 ID 重建，并把“移动一个 Page”的副作用扩大到 Notebook lifecycle。若未来产品需要显式 Notebook checkpoint，它必须是单独设计、单独授权、精确 confirmation 且明确披露 ID remap/关闭重开影响的 lifecycle 能力，不能隐藏在任意业务 mutation 中。

Manual-validation 只操作本次新建的 disposable Notebook，因此 Recipe 可以静态声明持久化 checkpoint。该特例用于构造可靠测试输入，不扩展生产 tool 权限，也不能推广为所有 Notebook 或所有 OneNote 版本的普遍要求。

## 5. HRESULT、重试与恢复建议

错误分类使用最内层 COM HRESULT；PowerShell wrapper HRESULT、异常深度和最内层异常类型只作为 content-free 诊断。不能仅凭 wrapper `0x80131501` 决定重试或要求用户关闭 Notebook。

| Reconciliation | Typed evidence | 调用方动作 |
| --- | --- | --- |
| `applied` | 完整 postcondition 成立 | 成功；若 execute 抛错，标记 reconciled success |
| `not_applied` | 精确 pre-state + not-yet-synchronized/file unavailable | 可建议用户在 OneNote 中显式关闭并重开后重新发起新调用 |
| `not_applied` | modal UI | 关闭阻塞对话框后重新发起新调用 |
| `not_applied` | 确定性非法请求 | 修正输入，不建议原样重试 |
| `partially_applied` | 任意 | 禁止重放；返回 current IDs/位置和人工恢复要求 |
| `indeterminate` | 任意 | 禁止重放；先用只读 Tool 重新查询实际状态 |

通用 reconciliation 基础设施允许个别真正幂等的 mutation 在严格条件下重试，不代表 Page Reparent 获得重放许可。每个 operation 必须独立声明 replay policy；未知、partial 或 identity-remapping mutation 默认不重放。

## 6. 当前实现与目标实现边界

当前已经成立的合同包括：typed confirmation、进程内写协调、连续稳定 read-back、Reparent 两阶段 hierarchy/full-evidence 验证、最内层 HRESULT 诊断，以及生产 Reparent 不依赖 `SyncHierarchy` 或自动 close/reopen。Disposable `reparent-page` Recipe 已使用持久化 checkpoint，并有用户运行的 fresh/cache 成功证据。

尚未完成、由 [TODO 029](../todo/029_mcp_mutation_readiness_and_reconciliation_hardening.md) 跟踪的生产加固包括：

- Page Reparent execute 异常后的四态 reconciliation；
- `applied` reconciled success；
- `not_applied/partially_applied/indeterminate` 的稳定错误字段与恢复建议；
- 主 Reparent 单次调用、无 Sync/Close/Open/filesystem probe 的负合同；
- 对其他 MCP mutation tools 的 readiness/replay policy 审计矩阵。

在 TODO 完成前，本文的状态模型是设计约束，但不能把上述目标字段或错误分支表述为已经实现的公开响应。
