# 051：常驻 COM Client 在 OneNote 重启后的代理刷新

> ID：051
> 状态：已完成
> 优先级：P1
> 类型：OneNote COM / Bridge Transport / 生命周期恢复
> 更新日期：2026-08-22

## 背景

默认 `persistent_powershell` adapter 会在一个 STA PowerShell host 内长期持有一个 `OneNote.Application` COM 对象。这个对象属于 host，而非 Python 的 `OneNoteBridge`、Runtime、service 或 MCP tool；正常请求通过 host 的串行 framing 转发，不跨进程暴露 COM proxy。

已观察到一个恢复缺口：常驻 host 存活期间，OneNote Desktop 可能意外退出。随后用户可经内部 `launch_onenote_gui` 工具重新启动并确认 GUI ready，但 host 内原先持有的 COM 对象仍指向退出前的 OneNote 实例。若该对象已失效，后续业务请求可能得到断连/陈旧 proxy 错误，而不是使用重启后的 OneNote 实例。

当前实现把 `{ok:false}` 的 COM 响应视为已响应的业务失败，不根据 HRESULT 推测 proxy 是否仍然可用；只有 timeout、EOF、协议错误等 transport/protocol 故障才会 poison host generation 并在后续请求中创建新 host。这一保守语义正确地避免了对不确定 mutation 的隐式重放，但不能解决“GUI 已通过内部工具恢复、host 仍保留旧 COM 对象”的显式恢复路径。

## 目标

1. 当 OneNote Desktop 经受控的内部启动路径恢复且 GUI ready 后，常驻 client 能获得一个可用于后续请求的 fresh `OneNote.Application` 对象。
2. 保持 COM proxy 的单一 execution owner：Runtime、service、tool 和 Python 调用线程均不得直接持有、替换或跨边界传递 COM 对象。
3. 保持当前 request delivery、mutation reconciliation 和 fail-closed 语义：恢复过程不得自动重放任何正在执行或已失败的业务 operation，尤其不得据此推断 mutation 未发生。
4. 不改变公开 MCP tool 参数、mutation policy、GUI readiness 门限、typed ID 选择或现有 one-shot fallback 的显式选择规则。

## 路径比较与选型

| 路径 | 安全性 | 耦合 | 恢复语义 |
| --- | --- | --- | --- |
| Host 原地刷新 `$onenote` | COM owner 不变；dispatch lock 阻止业务观察中间阶段；失败才 discard generation | 新增 `kind=refresh_com` 控制帧与 `com_epoch`，不改协议版本 | GUI ready 只允许尝试 refresh，不证明 backend 与 GUI 同 PID |
| Python 淘汰整个 host | 扩大恢复影响面；不能额外证明新 COM 与 GUI 一致 | 复用既有 poison/reap | 把 GUI ready 误建模为必须退休 generation |

**选定**：同 host 原地刷新 + 浅层可调用性 probe 为主；confirmed host discard 只作为 refresh 失败后的兜底。不新增 `REFRESHING`，不做 GUI/COM PID 比对，不根据 HRESULT 自动恢复，不重放业务请求。

GUI readiness 是操作稳定性前提，不是 COM backend 身份证明，因此不采纳“GUI ready 后淘汰整个健康 host”。

## 最小实现边界

- 唯一触发点：`launch_onenote_gui` 在 GUI ready（`started` 或 `already_running`）后调用非公开 `OneNoteBridge.refresh_com_client()`。
- host 内：best-effort 释放旧 proxy → activation → `$onenote.Windows` + `[uint64]$windows.Count` → `finally` 释放 `$windows` child RCW → 提交 `com_epoch=k+1`。
- `refreshed` 只声明“新 proxy 此刻可完成浅层 COM 调用”。
- `com_epoch` 嵌套于 host generation，只在 `READY` 内有效；client 在新 generation ready 后重置为 `1`，成功刷新校验严格整数且恰为 `k+1`。
- 完整两段 admission + 统一 post-submit 终态提交；与 `close()` 的线性化由 state lock 定义，提交 `BROKEN` 时同时确定 cleanup owner。
- reader 线程只把状态标为 `BROKEN`，不承担物理回收；confirmed reap 必须看到 process 已退出且 reader 已真正结束。
- generation cleanup 由 `_cleanup_lock` 串行化。refresh failure 提交 `BROKEN` 时同时声明 cleanup owner；`close()` 观察到 refresh-owned `BROKEN` 时等待其收敛，再完成关闭。不得在 refresh 已提交 `BROKEN` 后把终态改写成 `rejected_closed`。
- `close()` 未确认时停在 `CLOSING`，保留 handle 与重试路径，不得进入 `CLOSED` 并失去收敛。
- 结果：`refreshed` / `not_needed` / `rejected_closed` / `not_attempted(reason)` / `host_discarded` / `host_discard_unconfirmed`。
- `host_discarded` 仅在目标 generation 已 confirmed reap 后使用，投影 `discarded_generation`。
- refresh audit 只写 `refresh_outcome` 与 content-free 投影，不写泛化 `ok`。
- 公开契约仅增加 `launch_onenote_gui` 的 content-free `com_client_refresh` 字段；刷新失败不推翻 launch 成功。

当前合同见 [常驻 OneNote COM Client Bridge 状态模型](../design/persistent_com_client_bridge.md) 第 8 节。

## 风险与待决问题

- GUI-ready 证据仍不足以代表新 COM activation 已可用于任意业务 API；刷新失败单独投影，不误报 launch 成功。
- 原 `$onenote` 已断连时的 release/recreate 由 host 内 best-effort + probe 处理；失败 discard generation。
- 断连发生在业务 operation 中时，必须保留 `possibly_dispatched`/`indeterminate` 的既有语义。
- 真实 OneNote 关闭 → 内部启动 → 后续 read 已由独立 GUI 入口证明；`run-2026-08-22-20-24-41` 又完成可恢复 mutation 闭环，覆盖三个 COM owner 刷新与 probe、重启后 baseline 稳定、耐久 rename、restore，以及 MCP teardown 前 exact Notebook close。
- 验证框架实际有三个独立 COM owner：MCP child persistent client、harness `_internal_bridge`，以及 `NotebookLifecycleWrapper` 的 lifecycle bridge。`launch_onenote_gui` 只刷新第一个。OneNote 退出后若不刷新后两个，mutation 后的 XML read 或最终 exact close 可能得到 `0x800706BA`。
- 2026-08-22 `run-2026-08-22-19-58-52` 已证明三个 COM owner 均可刷新，且 failure finalizer 能关闭 disposable Notebook，未再出现 `0x800706BA` 或 traceback。失败点是验证框架过早把两次短间隔观察当成 mutation 已收敛：前向 `rename_page` 返回成功且 `after.json` 见到 marker，随后 restore 前置确认读回原始标题 `00-Owned-Page`，restore `attempts=0`。这是 harness 收敛判定问题，不是 COM refresh、重复 mutation 或 lifecycle close 问题。当前 harness 在 refresh/probe 之后、mutation 之前增加有界目标页 baseline 稳定门禁，并在前向 rename 之后做更充分的有界耐久观察；marker 若回到原始标题则记为 `forward_not_durable`，不调用 restore，也不重放 forward rename。

## 预期验证

- 确定性 fake-host 合同：同一 host 内刷新后业务响应携带新 `com_epoch`；host generation、sequence 与单飞约束保持正确；
- 确定性负向合同：activation 与 probe 失败分开注入、malformed epoch、timeout/EOF、client 已关闭、unconfirmed reap、unconfirmed close 可重试均 fail closed，且不重放此前或当前业务 request；
- 生命周期合同：reader 真正结束后才能进入 `NEW`；refresh failure 与 close 竞争只有一个 cleanup owner；refresh 已提交 `BROKEN` 后 close 介入必须仍返回 `host_discarded`/`host_discard_unconfirmed`；pending 已发布或 `{ok:false}`/malformed 已到达但尚未提交失败时，close 胜出返回 `rejected_closed`；
- 控制面合同：未授权时 refresh hook 零调用；GUI 未 ready、launch timeout 或启动失败时不得触发刷新；one-shot 与未启动的 persistent client 不引入额外 GUI/COM activation；launch 成功但 refresh 为 `host_discarded`/`host_discard_unconfirmed`/`not_attempted` 时公开 launch 仍成功；
- 审计合同：`not_needed` 与各失败 outcome 均写入 refresh audit，含 `refresh_outcome` 且不含泛化 `ok`；
- 人工验证由用户在 disposable 本地 OneNote 环境中执行：使已有 persistent client 建立后关闭 OneNote，经 `launch_onenote_gui` 恢复，再验证后续只读调用和一个受 policy 控制的成功 mutation。Agent、pytest、CI、hook、import、timer、watcher 与 dry-run 不得触发该真实流程。

## 完成定义

- [x] 已完成并记录“PowerShell host 原地刷新”与“Python host 重建”两种路径的安全性、耦合和恢复语义比较；
- [x] 已选择最小实现边界，明确 client 契约、控制请求转发、状态投影和失败语义；
- [x] 自动化 fake-host 合同覆盖 fresh object、断连/刷新失败、无业务重放及 content-free audit；
- [x] 当前设计文档、TODO 048 与相关公开契约已按最终实现同步；
- [x] 用户确认独立 GUI 入口的关闭 → 内部启动 → 后续只读恢复、repeated refresh 与 enabled-MCP 单窗口 verdict；
- [x] 用户确认 `com-refresh-mutation` 的关闭 → 同一 MCP 恢复 → harness internal 与 lifecycle COM 刷新及精确 probe → 目标页 baseline 稳定 → 唯一 rename → 耐久观察确认 marker 未回退 → restore → 在 MCP teardown 之前完成 exact Notebook close，且不存在重复 mutation、`forward_not_durable`、`0x800706BA` close 或 traceback 退出。

## 人工验收命令（仅用户执行）

独立 GUI 入口验证同一 MCP 进程内的 host 建立、warm refresh、人工关闭 OneNote 后恢复，以及 repeated `already_running → refreshed`。它不得 mutation：

```powershell
.venv\Scripts\python.exe tests\manual_validation\launch_onenote_gui_check.py --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\launch_onenote_gui_check.py --verbosity verbose
```

可恢复 mutation 由独立具名 scenario 承担，不得扩权上述 GUI 入口，也不得由 pytest、CI 或 agent 真跑：

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py com-refresh-mutation --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py com-refresh-mutation
```

期望：

- GUI 入口在用户确认关闭后，必须在既有 timeout 内有界轮询 native `health_check`，直到进程与可见窗口都不存在，才能恢复；窗口消失但 `ONENOTE.EXE` 仍在必须继续等待。`host_discarded(g)` 之后的新 generation 必须大于 `g`。最终 ready health 与单窗口人工 verdict 必须在 enabled MCP 仍存活时完成；teardown 后 OneNote 状态为 `not_asserted`。
- GUI 入口只读恢复、repeated refresh 与单窗口 verdict 已由用户确认通过。
- `com-refresh-mutation` 在同一 MCP 上证明：fixture/host 建立 → 用户关闭 → 相同的 bounded native fully-stopped wait → 一次 `retry_read=False` launch 恢复 → 分别刷新 harness `_internal_bridge` 与 `NotebookLifecycleWrapper`，并对 owned Page / leased Notebook 做精确只读 probe → 有界目标页 baseline 稳定后唯一 marker rename 恰好一次 → 有界耐久观察确认 marker 未回退 → restore → 同一 lifecycle wrapper 在 MCP/internal teardown 之前完成 exact close，外层 finalizer 只核验 pre-closed lease。MCP child refresh 不能代替后两个 owner；任一 refresh/probe/baseline 失败必须在 mutation 前 fail closed；若 marker 回到原始标题，记为 `forward_not_durable` 且不得 restore 或重放 forward rename；post-snapshot 或 finalization 失败不得重放 rename，也不得按 HRESULT 自动恢复。finalization 失败必须写入 `run-failure.json` 与完整 lifecycle evidence，并以受控非零退出结束。
- 2026-08-22 真实 mutation 运行 `run-2026-08-22-18-24-42` 已证明 MCP refresh 与 `rename_page` 成功，但 stale `_internal_bridge` 在 post-snapshot `get_page_xml` 上返回 `0x800706BA`。该现场已关闭 disposable Notebook 并保留，不得自动清理。
- 2026-08-22 后续运行 `run-2026-08-22-19-32-34` 已证明 MCP/internal refresh、rename、read-back 与 restore 成功，但 stale lifecycle bridge 在最终 close 上返回 `0x800706BA`，且 `run.py` 直接 traceback。页面标题已恢复；lifecycle lease 仍为 active。该现场继续保留，不得自动清理。
- 2026-08-22 后续运行 `run-2026-08-22-19-58-52` 已证明三个 COM owner 刷新与 failure-finalizer exact close 成功，且无 `0x800706BA` / traceback。前向 rename 曾见到 marker，但 restore 前置确认读回原始标题且 restore 未提交。该现场继续保留，不得自动清理。
- 2026-08-22 后续运行 `run-2026-08-22-20-14-29` 已证明 baseline 稳定、forward rename 耐久和 restore 成功；失败点是 MCP/internal teardown 后再做 lifecycle close，得到 `0x800706BA`，且 pre-submit identity 失败被写成 `close_failed` 后 failure finalizer 再次拒绝。该现场继续保留，不得自动清理。当前 harness 改为在 MCP context 内 exact close，外层只核验 pre-closed lease；pre-submit 失败保留 active 并记录 `close_not_submitted`。
- 2026-08-22 用户确认最终运行 `run-2026-08-22-20-24-41` 通过：同一 MCP 在 OneNote fully stopped 后成功恢复；MCP child、harness internal 与 lifecycle 三个 COM owner 均完成 refresh/probe；目标页 baseline 稳定，forward rename 经耐久观察未回退，随后 restore 成功；lifecycle wrapper 在 MCP teardown 前完成 exact close，外层 finalizer 接受 durable pre-closed lease，最终 `run-state.status=passed`、`lifecycle-bundle.status=closed_preserved`。至此完成定义全部满足，TODO 051 关闭。

`host_discard_unconfirmed`、`not_attempted` 或 `rejected_closed` 不算恢复验收成功。

## 关联

- [TODO 048](048_persistent_com_client_bridge.md)：当前 client-adapter、generation、delivery state 与 host 故障收尾合同；本项补足 OneNote 进程重启后 stale COM proxy 的恢复问题。
- [TODO 031](031_start_onenote_desktop_tool.md)：受控、最多一次的 OneNote GUI 启动与 readiness 边界。
- [常驻 OneNote COM Client Bridge 状态模型](../design/persistent_com_client_bridge.md)：当前 host generation、COM epoch、pending request 与不重放约束。
- [TODO 025](025_onenote_com_convergence_and_mutation_coordination.md)：COM failure、收敛与 mutation reconciliation 的既有边界。
