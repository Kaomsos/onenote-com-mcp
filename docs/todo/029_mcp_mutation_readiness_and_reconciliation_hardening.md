# 029：MCP Tool Mutation Readiness 状态建模与 Page Reparent 加固

> ID：029
> 状态：待办
> 优先级：P1
> 类型：生产可靠性 / MCP Tool 生态 / Mutation 安全
> 更新日期：2026-08-13

## 目标

把 [`mutation_readiness_and_call_design.md`](../design/mutation_readiness_and_call_design.md) 的平台限制落实到生产 MCP tool：不再试图用 `SyncHierarchy`、稳定 snapshot、sleep 或 filesystem 状态伪造 `mutation_ready`，而是明确 `logical_ready → execute once → reconcile actual outcome` 的调用合同。

第一阶段以 `reparent_page` 为完整纵向切片，解决本次 Page Reparent 暴露的生产风险；第二阶段审计其他 mutation tools 的 readiness、identity remap 与 replay policy，形成 MCP 生态一致但 operation-specific 的矩阵。不得把 Page Reparent 的“禁止重放”或 disposable checkpoint 无差别套用到所有工具。

## 已有基础与缺口

已有基础：

- typed ID、confirmation、同 Notebook 与 scope 验证；
- 进程内 mutation 独占协调；
- Reparent 两次稳定 hierarchy + 完整 evidence/bookend read-back；
- typed HRESULT 与最内层 COM HRESULT、wrapper HRESULT 诊断；
- 生产 Reparent 不依赖 `SyncHierarchy`，也不会自动 close/reopen；
- disposable `reparent-page` v3 checkpoint 已由用户 fresh/cache 真实运行确认。

仍需实施：

- `reparent_page` 主 execute 异常后的 `applied/not_applied/partially_applied/indeterminate` 完整对账；
- execute 报错但完整 postcondition 成立时的 reconciled success；
- 稳定、content-free、Agent 可行动的阶段、重试安全和恢复建议字段；
- 主 Reparent mutation 只调用一次，且生产路径绝不调用 Sync/Close/Open/filesystem readiness probe 的负合同；
- 其他 mutation tools 的 readiness/replay policy 生态审计。

## 阶段 A：`reparent_page` 生产加固

### Logical preflight

- [ ] 冻结 live typed Page、source/destination Section、同 Notebook、活动态、confirmation、scope、内容基线、预算和 hierarchy bookend；
- [ ] 内部状态只命名为 `logical_ready`，不公开或推导 `mutation_ready=true`；
- [ ] preflight 失败统一记录 `mutation_stage=preflight`、`mutation_attempted=false`、`mutation_replayed=false`；
- [ ] confirmation/read-back 继续绕过未来 TTL read cache，使用 live 状态。

### Execute-once 与 reconciliation

- [ ] 主 Reparent `UpdateHierarchy` 调用次数固定为 1，任何 HRESULT 均不自动重放；
- [ ] execute 返回成功或异常后都用同一个 operation-specific observer 读取实际状态；
- [ ] 完整 destination、唯一 ID map、scope、内容和无关对象 postcondition 成立时分类 `applied`；
- [ ] execute 抛错但 `applied` 时按成功返回，并增加 `execute_error_reconciled=true`、`mutation_attempts=1`、`mutation_replayed=false`；
- [ ] 只有完整 frozen pre-state、无 fresh/removed/remapped Page、无 destination candidate、无 promotion/其他变化时分类 `not_applied`；
- [ ] descendant promotion 已发生、出现部分 topology/identity/content 变化或完整 postcondition 不成立时分类 `partially_applied`；
- [ ] 读取失败、目标歧义、bookend 震荡或证据不足时分类 `indeterminate`；
- [ ] reconciliation 最多重试一次只读取证，绝不重放 mutation。

### 错误与恢复合同

- [ ] 失败 details 稳定包含 `mutation_stage`、`mutation_attempted`、`mutation_attempts`、`mutation_replayed`、`observed_outcome`、`preflight_state`、`persistence_checkpoint`、`retry_safety` 和 `recommended_action`；
- [ ] `not_applied` 且最内层 HRESULT 为 not-yet-synchronized/file unavailable 时，可建议用户在 OneNote 中显式关闭并重开后发起新调用；
- [ ] modal UI 只建议关闭对话框；确定性非法请求要求修正输入；
- [ ] `partially_applied` 和 `indeterminate` 明确禁止重放，并要求使用只读 Tool 查询 current ID/位置或人工恢复；
- [ ] 不根据 wrapper `0x80131501`、错误消息字符串或固定等待推断 retry safety；
- [ ] response/audit 不包含 Page 正文、raw XML、binary、完整路径、secret 或原始参数。

### Lifecycle 与权限负合同

- [ ] 生产 `reparent_page` 不调用 `sync_hierarchy`、`close_notebook`、`open_hierarchy` 或 filesystem Notebook readiness probe；
- [ ] 不新增 lifecycle、Delete、Copy、Move、Raw XML 权限，不修改现有公开参数；
- [ ] 不把 manual-validation 的 disposable checkpoint 迁入生产业务 tool；
- [ ] 若未来需要公开 `checkpoint_notebook`，另立设计/TODO并进行独立权限、confirmation、UI/ID remap 审查，本 TODO 不实现。

## 阶段 B：MCP mutation tool 生态审计

- [ ] 为 Create、Page content mutation、Rename、Reorder、三类 Reparent、Copy/Move、Delete 和 Close 建立 readiness/replay policy 矩阵；
- [ ] 每类 operation 明确 logical precondition、allocated/remapped identity、execute attempts、reconciliation observer、partial boundary 和是否允许 replay；
- [ ] 没有显式 operation policy 的 mutation 默认 `mutation_replayed=false`；
- [ ] `SyncHierarchy`、OpenHierarchy object ID、单次 snapshot 或 COM success 不得被任何 tool 文档写成完成证明；
- [ ] 已由 TODO 025 完成且合同充分的工具不机械重写，只补缺口和负合同；
- [ ] 将最终矩阵同步回 design/tool contracts，TODO 不成为唯一权威来源。

## 自动化验证

至少覆盖：

- [ ] 最内层 HRESULT 穿透 wrapper，分类不受 wrapper `0x80131501` 覆盖；
- [ ] execute 异常 + 精确 pre-state → `not_applied`；
- [ ] execute 异常 + 完整 postcondition → reconciled success；
- [ ] Page 已移动但内容或无关对象 invariant 失败 → `partially_applied`；
- [ ] descendant promotion 后主 Reparent 失败 → partial，不能判为整体 `not_applied`；
- [ ] reconciliation 读取失败、候选不唯一或 hierarchy 震荡 → `indeterminate`；
- [ ] 所有路径主 Reparent mutation 调用次数恒为 1；
- [ ] 不调用 Sync/Close/Open/filesystem probe；
- [ ] 所有失败有非空阶段、outcome、retry safety，且 audit/response content-free；
- [ ] 现有成功、ID remap、root-only/subtree、restore 和两阶段 read-back 合同不回退；
- [ ] 聚焦纯测试与完整 `.venv\Scripts\python.exe -m pytest -q` 通过。

## Human-gated 真实验证

生产实现完成后，继续使用现有具名 `reparent-page` 场景验证正常成功路径；fixture checkpoint 只构造可靠 disposable 输入，production bridge audit 必须证明业务 tool 未调用 Sync/Close/Open。用户应分别运行 fresh 与 `--use-cache`，确认正向、ID remap、内容/无关对象、恢复和 lifecycle 全部通过。

错误分支不得为了验收而人为破坏真实 OneNote 或制造 modal dialog。`not_applied/partial/indeterminate` 主要由 deterministic fake timeline 覆盖；若用户自然遇到相应真实 HRESULT，可补充脱敏 evidence，但不是通过危险操作制造失败的前置条件。Agent 绝不能执行真实 scenario。

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
- 阶段 B 形成并审查 operation-specific readiness/replay policy 矩阵，发现的 P0/P1 缺口已修复或拆分为明确后续 TODO；
- 公开参数保持兼容，新增字段为已记录的稳定 additive contract，文档与 README 同步；
- 聚焦测试和完整 pytest 通过；
- 用户在实现后的当前版本显式确认 `reparent-page` fresh 与 cache 真实回归通过；
- 真实证据未完成前不得将本 TODO 标记为“已完成”。

## 关联

- [Design：Mutation Readiness 状态模型](../design/mutation_readiness_and_call_design.md)
- [Lesson：Mutation Readiness 不能由 Preflight 证明](../lesson/onenote_mutation_readiness_is_not_preflight_observable.md)
- [TODO 025：公共 convergence/reconciliation/coordination 基础设施](025_onenote_com_convergence_and_mutation_coordination.md)
- [TODO 027：Reparent 人工验证矩阵与 checkpoint 真实证据](027_reparent_manual_validation_all_coverage.md)
