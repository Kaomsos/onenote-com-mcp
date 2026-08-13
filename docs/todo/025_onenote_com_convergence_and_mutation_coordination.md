# 025：OneNote COM 收敛、Mutation 对账与调用协调

> ID：025
> 状态：已完成
> 优先级：P1
> 类型：生产可靠性 / OneNote COM 一致性 / Mutation 安全 / Manual Validation
> 更新日期：2026-08-13

## 决策摘要

生产 MCP tools 通常操作已经打开且已进入 OneNote COM hierarchy 的对象，不会普遍经历 fixture cache working copy 首次打开时的整棵 hierarchy 接管与 ID 重建；但它们仍暴露在同一个 OneNote Desktop COM 异步边界下。COM mutation 返回不等于 hierarchy、Page XML、对象 ID 或 Notebook open state 已经稳定可读，紧邻的 read-back 或下一次 Tool 调用可能短暂观察到旧状态、不可查询对象或中间状态。`0x80042030` 还明确表示 OneNote modal UI 正在阻塞应用，而不是普通业务失败。

当前生产实现已经使用精确 ID、confirmation field、allocated-ID-first 和部分有界 read-back，但策略分散且不一致：Create、Rename、Delete、Close 与部分 Reparent 有轮询；Page content mutation、Reorder、Copy 最终 topology 验证、`sync_notebook` 和 `open_hierarchy(create_type=none)` 等路径仍包含一次性 read-back 或仅接受 COM 返回。每次 bridge operation 还会启动独立 PowerShell 进程和 `OneNote.Application` COM 对象，跨 Tool 调用没有统一事务、稳定门或进程内 mutation 协调。

本 TODO 将这些行为沉淀为生产层公共基础设施：保留 HRESULT 的 typed error、deadline-based convergence、operation-specific mutation reconciliation，以及覆盖完整“确认→COM mutation→稳定 read-back”的进程内读写协调。目标不是让所有错误自动重试，而是可靠地区分“未应用、已应用、部分应用、状态不确定”，只在证明仍是精确 pre-state 且操作允许时重试同一动作；否则继续 fail closed 并保留可执行恢复信息。

Fixture cache clone 的 materialization/open readiness 由 fixture cache/lifecycle 工作单独治理；OneNote ID 进入 cleanup evidence 文件名的问题也不属于本 TODO。本 TODO 只负责可复用于生产 MCP tools 的 COM 时序语义，并允许 manual-validation 调用同一生产机制验证，而不是维护第二套独立等待协议。

## 实施进度（2026-08-13）

- 已新增 `onenote_errors.py`、`services/convergence.py`、`services/reconciliation.py` 与 `services/coordination.py`；bridge/service/response 保留 typed HRESULT、retryability、partial 和 reconciliation。官方 `0x8004201D/23/30` 与 object/file unavailable HRESULT 已映射为稳定类型；只有明确 typed transient read 才进入 convergence，modal/object/file unavailable 均不构成 mutation replay 依据。
- 所有公开 Tool 通过进程级 reader/writer coordinator；mutation 从 confirmation 前进入独占区，generation invalidation hook 在任何 COM mutation 前执行，为 TODO 024 保留无旧读回填的接入点。首版仍明确不覆盖其他 MCP 进程或 Desktop 直接修改。
- Create/open、Page title/content、Rename、Page/Section Reorder、Reparent、Copy Page fidelity/最终 topology、Delete、Move 源缺席与 Close 已迁移到公共连续稳定观察；`sync_notebook` 改为 `accepted=true/converged=false` 请求语义。
- 已新增 fresh-only、`included_in_all=false` 的 `onenote-convergence` scenario 与独立最小 recipe；它直接验证生产 Create→Page update→Reorder→非永久 Delete timing，并由共享 lifecycle 记录 Close 证据。
- README、当前架构、Tool contract 和 manual-validation 文档已同步。自动化、dry-run 与用户前台真实证据均已闭合。

### 已闭合的真实后端证据

2026-08-13，用户在交互式前台显式运行并确认以下 HUMAN-GATED 场景；Agent 只读取已保存的 content-free/结构化 evidence，没有启动、续跑或清理任何真实 scenario：

- `run-2026-08-13-15-50-42` / `onenote-convergence`：passed；Create、Page update、Reorder、非永久 Delete 均为 `attempts=2/stable_observations=2`，fixture `restored=true`；共享 lifecycle Close 同样连续稳定两次并关闭 Notebook。
- `run-2026-08-13-15-54-30` / `create`：passed；两个同标题 Page 均保留 fresh allocated/read-back ID，各自连续稳定两次，默认精确非永久 cleanup 后 `restored=true`，Notebook 已关闭。
- `run-2026-08-13-15-56-46` / `reorder-page`：passed；正向与默认恢复均通过，`restored=true`，Notebook 已关闭。
- `run-2026-08-13-15-58-04` / `delete`：passed；预期非永久删除终态通过，`restored=false` 为场景合同语义，Notebook 已关闭。
- `run-2026-08-13-15-58-25` / `copy-page`：passed；同 Section、跨 Section、跨 Notebook的 root-only/subtree 共六个 case 全部 `verified=true/lossless=true/partial=false`，最终 topology 均连续稳定两次；反向 cleanup 后双侧 `restored=true`、双 Notebook 已关闭。
- `run-2026-08-13-16-05-59` / `move-page`：passed；跨 Notebook root-only/subtree 两个 case 均 `outcome=moved`、Copy verified/lossless、`source_deleted_nonpermanently=true/partial=false`；源删除只在 Copy 门通过后发生，双 Notebook 已关闭。

六次运行均 `observed_mcp_process_starts=1`、`agent_execution_prohibited=true`，没有 permanent delete、Raw XML、第二套 Copy target 或 source-delete 越权证据。正常后端下公共连续稳定门未出现不合理超时，completion definition 要求的真实 convergence 与受影响回归证据据此闭合。

### 已闭合的自动化证据

- `.venv\Scripts\python.exe -m pytest -q`：`918 passed`（2026-08-13，最终复验）；唯一 warning 是 sandbox 无权写 `.pytest_cache`，不影响测试结果。
- `.venv\Scripts\python.exe tests\manual_validation\run.py onenote-convergence --dry-run --json`：通过；`agent_execution_prohibited=true`、`server_started=false`、fresh-only、单 MCP 计划、最小 policy/tool allowlist、`included_in_all=false`，未访问 cache、未创建目录、未执行真实 OneNote mutation。

## 当前风险基线

### 已有较强 read-back 的路径

- `create_notebook`、`create_section`、`create_section_group`、`create_page` 使用 mutation 前 ID 集、COM allocated ID、唯一 fresh path remap 和有界 `wait_for_created`；
- Page title update 与 Rename 使用精确 ID 和 predicate 轮询；
- Delete、Close 和 typed Reparent 已有有界状态回读，其中 partial mutation 会保留结构化失败；
- Copy/Move 已保留 allocated/created/resolved IDs，Copy 未验证时阻止 Move 删除 source。

### 仍需统一治理的路径

- `open_hierarchy(create_type=none)` 在目标未预先出现在 hierarchy 时只返回 COM object ID，不证明该对象随后可查询、类型/路径/parent 正确或 ID 已重映射；
- `reorder_page`、Section/SectionGroup Reorder 在 `UpdateHierarchy` 后主要依赖一次 hierarchy snapshot，可能把暂时未收敛误报为永久失败；
- `append_to_page`、`add_image_to_page`、`replace_page_body`、`delete_page_content` 在写后立即读取 Page XML/对象集合，缺少统一稳定等待；
- Copy 的最终 topology 和部分 Page read-back 仍可能在 OneNote 尚未收敛时返回 `copy_unverified`；Move 必须继续把任何无法证明的 Copy 结果保持为 `copy_only`；
- `sync_notebook` 只证明 COM 调用返回，不声明同步完成；公开响应需要明确区分 accepted 与 converged；
- bridge 已读取 HRESULT，但 `BaseService` 当前会把 `OneNoteBridgeError` 折叠成普通 `RuntimeError`，Tool response 通常只能返回泛化 `backend_error`；
- MCP 进程内没有覆盖完整 service mutation 的读写协调器。单个 Tool 内代码按顺序执行，但多个 Tool 请求若并发进入，可能在另一个 mutation 的中间状态上读取或写入；
- 当前多个文件分别维护 `for range(8) + sleep(0.5)`，缺少统一 deadline、连续稳定观察、attempt evidence 和可测试 clock。

## 目标代码结构

建议形成以下职责边界；最终文件名可以在实现审查中调整，但职责不得重新散落回各 Tool：

```text
src/local_onenote_mcp/
├─ bridge.py
├─ onenote_errors.py
├─ services/
│  ├─ coordination.py
│  ├─ convergence.py
│  ├─ reconciliation.py
│  ├─ hierarchy.py
│  ├─ mutations.py
│  ├─ copying.py
│  └─ operations.py
└─ tools/
   └─ responses.py
```

### HRESULT 与 typed error

- `bridge.py` 保留原始 signed/unsigned HRESULT、操作名和 content-free category，不把 Page 正文或完整 bridge 参数写入错误或 audit；
- `onenote_errors.py` 定义稳定的生产错误类型与 retryability，例如 modal UI blocked、not yet synchronized、timeout、object/file unavailable 和 unknown backend error；
- 至少将 `0x80042030` 投影为 `onenote_modal_ui_blocked`，不得把它误报为 Copy fidelity、cache corruption 或普通 validation error；
- `BaseService` 与 `tools/responses.py` 保留 typed error，不再丢失 HRESULT；公开响应契约明确 `error_type/code`、`hresult`、`retryability`、`partial` 和 reconciliation 状态；
- 只允许基于 HRESULT 与精确后置状态决定 retryability；不能仅按错误消息字符串或“重新运行可能成功”推断。

### Convergence helper

- `services/convergence.py` 提供 monotonic deadline、可注入 clock/sleeper、连续稳定观察和有界 observation history；
- convergence 以 operation-specific `observe`、`accept` 和 stable identity projection 工作，不内置 Copy、Reparent 或 Page fidelity 业务规则；
- 默认必须要求至少两个连续一致且满足 postcondition 的观察，避免单个瞬时 snapshot 被当成稳定状态；具体 deadline、间隔和最大 observation 数必须有统一配置或固定合同，不允许各 Tool 随意复制 magic numbers；
- 成功返回 attempts、elapsed、stable observations、identity remap 和 content-free transient error 分类；失败返回最后可证明状态，但不泄露 Page XML、正文、binary 或用户路径参数；
- mutation confirmation、convergence 和 reconciliation 必须读取 live OneNote 状态，不得使用 TODO 024 规划的 TTL read cache。

### Mutation reconciliation

- `services/reconciliation.py` 定义统一结果：`not_applied`、`applied`、`partially_applied`、`indeterminate`；
- 每类 mutation 声明精确 precondition、execute、observe 和 postcondition，业务 service 继续拥有名称、parent、位置、Page digest、Copy topology 与 fidelity 判断；
- COM error 或 timeout 后先按精确 ID/allocated IDs 读取状态。只有状态仍等于已冻结 pre-state、没有新 allocated/created object 且该 operation 明确允许时，才能在同一次 Tool 调用中有界重试同一 mutation；
- postcondition 已成立时可把“COM 报错但已应用”收敛为成功，同时返回 reconciliation evidence；
- 部分变化、目标身份歧义、无法读取或证据不完整时返回 `PartialFailure`/indeterminate，禁止重做整个 Copy/Move、创建第二套目标或删除 source；
- Create/Copy/Move 必须把 allocated、created、resolved IDs 纳入 reconciliation，复用已创建目标而不是再次按名称猜测。

### 进程内调用协调

- `services/coordination.py` 提供一个 MCP 进程级读写协调器；共享只读调用可以受控并发，mutation 对完整“confirmation→cache invalidation→COM execute→reconciliation/convergence”持有独占权；
- mutation 稳定 read-back 完成或 fail-closed 分类前不得释放独占权，避免另一 Tool 读取中间状态；
- 等待 coordinator 时必须受 Tool/bridge 总 timeout 约束，取消或异常必须可靠释放，不得形成永久锁；
- 首版只承诺单 MCP 进程内协调，不建立跨进程 daemon、文件锁分布式事务或 OneNote 全局锁；多个独立 MCP 进程和用户在 Desktop 中的直接修改仍属于外部并发，继续由 confirmation 和 reconciliation fail closed；
- 与 TODO 024 协作时，mutation 必须在 COM 前使只读缓存失效，并用 generation 防止旧读回填；协调器不能让 cached snapshot 进入 confirmation 或 read-back。

## 优先接入顺序

1. 保留 bridge HRESULT 并建立 typed response，不改变 mutation 重试语义；
2. 实现可独立测试的 convergence helper，替换现有分散的 `wait_for*`、Delete/Close/Reparent 等价轮询；
3. 优先加强 `open_hierarchy(create_type=none)`、Page content mutation、Reorder 和 Copy 最终验证；
4. 加入 operation-specific reconciliation，先覆盖 Page update、Create 和 Copy/Move 的 allocated-ID 路径；
5. 在所有生产 Tool 的 service 入口接入进程内 coordinator，并与 TODO 024 的 mutation-before-COM 失效顺序共同测试；
6. 统一 manual-validation timing evidence，使场景验证生产机制，不复制另一套 magic retry loop。

## 公开 Tool 合同

- 成功响应可增加稳定、content-free 的 `timing`/`convergence` 摘要，至少包含 attempts、elapsed、stable observations 和 identity remap；字段是否进入所有 Tool 或仅 mutation Tool，实施时必须一次定稿并同步 schema；
- typed backend error 必须保持 Agent 可行动：modal UI 提示用户关闭阻塞对话框；not-yet-synchronized 提示等待/重新读取；partial/indeterminate 明确禁止盲目重做；
- `open_hierarchy(create_type=none)` 不得再把“COM 返回 ID”单独表述为对象已 active。成功必须回读精确 live identity，或明确返回 accepted-but-not-converged 的非成功/不完整状态；
- `sync_notebook` 不得声称同步已经完成，除非存在可靠且受支持的完成证明；否则公开合同只声明同步请求已提交；
- 不保证用户在 OneNote Desktop 或其他 MCP 进程中的外部 mutation 被本进程事务化；confirmation mismatch 和 indeterminate 仍是合法、显式结果。

## 自动化合同

至少覆盖以下 fake bridge/clock 时间线：

- allocated ID 返回后暂时不可查，随后以原 ID 连续稳定；
- allocated ID 消失，唯一 fresh typed address 映射到新 ID并连续稳定；
- 一次满足 postcondition 后又回退，必须继续等待而不能提前成功；
- transient not-yet-synchronized/timeout 后达到 postcondition；
- `0x80042030` 保留 typed modal error 和 HRESULT，不自动重放存在副作用的 mutation；
- COM error 后状态仍精确等于 pre-state，允许的幂等写只重试同一目标一次；
- COM error 后 postcondition 已成立，reconciliation 返回 applied；
- Copy 已创建部分 target、Page update 部分完成或状态不可读取时返回 partial/indeterminate，且不创建第二套 target、不删除 source；
- Page XML mutation、Reorder、Copy topology、Delete 和 Close 均要求连续稳定 postcondition；
- `open_hierarchy(create_type=none)` 不再返回未经 read-back 的完成状态；
- 两个并发 mutation 严格串行，一个 read 不得观察另一个 mutation 的中间窗口；多个纯 read 在没有 mutation 时可以按合同共享；
- coordinator timeout、异常与取消后锁被释放，后续调用可以继续；
- TODO 024 cache generation 与 coordinator 顺序正确：COM 前失效，confirmation/read-back live，旧 read 不能回填；
- response/audit 不包含 Page 正文、raw XML、binary、secret 或完整请求参数。

聚焦测试通过后运行完整纯测试：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## 真实后端验证

新增或扩展一个具名、默认不进入 `all` 的 human-gated 场景 `onenote-convergence`，使用本次运行创建的 fresh-only disposable Notebook，不依赖 fixture cache。Agent 只能运行纯测试和：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py onenote-convergence --dry-run --json
```

真实命令只能由用户在交互式前台启动。场景至少验证 Create、Page content update、Reorder、Delete/Close 的 convergence evidence、精确 ID/parent/postcondition 和 lifecycle；Copy/Move 继续由已有具名场景验证 partial safety 与 source-delete 门。Modal UI 不要求场景自动制造或关闭真实对话框；`0x80042030` 的分类与禁止盲目重试必须有纯合同测试，若用户自然遇到该错误，可把脱敏后的真实证据补充为环境观察，但不得伪造稳定复现。

完成前还应由用户运行受影响的既有生产回归场景，确认正常后端下没有因连续稳定门造成不合理超时，并确认出现 partial/indeterminate 时不会自动重复 mutation。Agent 不得运行任何真实 scenario。

## 非目标

- 不在本 TODO 中修改 fixture cache 的 opaque copy、publish、materialization、working hierarchy activation 或失败现场生命周期；
- 不处理 OneNote ID 进入 cleanup evidence 文件名或其他路径预算命名问题；
- 不自动点击、关闭或绕过 OneNote modal dialog，不使用 GUI automation 掩盖 `0x80042030`；
- 不对整个 Copy/Move/Create 进行无条件重试，不按名称猜测已有 target，不降低 source-delete、fidelity、confirmation 或 partial failure 门；
- 不建立跨进程锁、后台 watcher、timer、COM daemon、云同步服务或 Microsoft Graph 依赖；
- 不直接读写 `.one`/`.onetoc2`，不改变 local-only 边界；
- 不把固定长 sleep 当作稳定性合同，也不以提高 timeout 代替 postcondition/reconciliation。

## 完成定义

- HRESULT typed error、公共 convergence、mutation reconciliation 和进程内 coordinator 按上述职责落地，原有分散时序代码完成审计与迁移；
- `open_hierarchy(create_type=none)`、Page mutation、Reorder、Copy/Move、Delete、Close 和 Sync 的 accepted/converged/partial 语义明确且有自动化覆盖；
- 所有 mutation confirmation 与 read-back 保持 live，并与 TODO 024 的失效/generation 合同兼容；
- 公共 response schema、README、`docs/design/architecture.md`、`docs/design/tool_contracts.md` 和 manual-validation 文档同步；
- 聚焦测试、完整 pytest 和 `onenote-convergence --dry-run --json` 通过；
- 用户显式运行并确认 `onenote-convergence` 以及受影响的既有真实回归场景，证明正常路径收敛、并发协调、错误分类和 partial safety 符合合同；
- 真实证据未闭合前不得将本 TODO 标记为“已完成”。

## 关联

- [TODO 014](014_recipe_fixture_validation_and_local_notebook_cache.md)：fixture cache 与隔离 working copy 的既有架构；clone activation 不由本 TODO 实施。
- [TODO 021](021_windows_fixture_cache_path_budget.md)：受管路径预算与结构化 path failure；不负责 COM convergence。
- [TODO 024](024_search_and_query_read_snapshot_cache.md)：只读 TTL cache、mutation-before-COM 失效与 generation；实施时必须共同审查协调顺序。
- [当前架构](../design/architecture.md)：实现后 coordinator、convergence 与 bridge error 边界的 canonical 归属。
- [公开 Tool 契约](../design/tool_contracts.md)：实现后 accepted/converged/partial、typed error 和 response 字段的 canonical 归属。
- [Manual Validation Runner](../../tests/manual_validation/README.md)：真实场景的权限、证据与 HUMAN-GATED 执行边界。
