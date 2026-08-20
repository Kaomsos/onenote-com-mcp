# 051：常驻 COM Client 在 OneNote 重启后的代理刷新

> ID：051
> 状态：待办
> 优先级：P1
> 类型：OneNote COM / Bridge Transport / 生命周期恢复
> 更新日期：2026-08-20

## 背景

默认 `persistent_powershell` adapter 会在一个 STA PowerShell host 内长期持有一个 `OneNote.Application` COM 对象。这个对象属于 host，而非 Python 的 `OneNoteBridge`、Runtime、service 或 MCP tool；正常请求通过 host 的串行 framing 转发，不跨进程暴露 COM proxy。

已观察到一个恢复缺口：常驻 host 存活期间，OneNote Desktop 可能意外退出。随后用户可经内部 `launch_onenote_gui` 工具重新启动并确认 GUI ready，但 host 内原先持有的 COM 对象仍指向退出前的 OneNote 实例。若该对象已失效，后续业务请求可能得到断连/陈旧 proxy 错误，而不是使用重启后的 OneNote 实例。

当前实现把 `{ok:false}` 的 COM 响应视为已响应的业务失败，不根据 HRESULT 推测 proxy 是否仍然可用；只有 timeout、EOF、协议错误等 transport/protocol 故障才会 poison host generation 并在后续请求中创建新 host。这一保守语义正确地避免了对不确定 mutation 的隐式重放，但不能解决“GUI 已通过内部工具恢复、host 仍保留旧 COM 对象”的显式恢复路径。

## 目标

1. 当 OneNote Desktop 经受控的内部启动路径恢复且 GUI ready 后，常驻 client 能获得一个可用于后续请求的 fresh `OneNote.Application` 对象。
2. 保持 COM proxy 的单一 execution owner：Runtime、service、tool 和 Python 调用线程均不得直接持有、替换或跨边界传递 COM 对象。
3. 保持当前 request delivery、mutation reconciliation 和 fail-closed 语义：恢复过程不得自动重放任何正在执行或已失败的业务 operation，尤其不得据此推断 mutation 未发生。
4. 不改变公开 MCP tool 参数、mutation policy、GUI readiness 门限、typed ID 选择或现有 one-shot fallback 的显式选择规则。

## 实现思路（尚未定稿）

以下方向仅用于后续设计和小范围 fake-host 验证，尚未决定具体实现：

1. **由 Python 识别恢复时点，由 COM owner 刷新对象。** `launch_onenote_gui` 在确认 GUI ready 后，可调用一个不公开的 bridge/client 生命周期 hook。该 hook 不作为业务 OneNote operation、不进入公开 tool surface，也不携带用户内容或对象 ID。
2. **优先评估 PowerShell host 原地重建。** 由持有 `$onenote` 的 STA host 负责释放旧引用（best effort）、清空旧对象并新建 `OneNote.Application`；Python 只接收受控成功/失败结果。该方向可保留 host、协议连接和串行 execution owner，避免把普通 COM refresh 等同于杀掉 PowerShell 进程。
3. **保留 Python 重建整个 host 的比较方案。** 若 host 无法可靠确认刷新完成，或刷新控制请求发生 transport/protocol 故障，可沿用已有 poison/reap → 新 generation 的机制。需要明确这仅是 host 故障收尾，不能作为成功刷新或前一业务请求未执行的证据。
4. **区分受控刷新与通用错误自动恢复。** 不应仅因任意 `{ok:false}` 或 HRESULT 自动重新执行原请求；是否把已知断连 HRESULT 用作“提示需要刷新”的诊断信号，需另行评估。任何方案都必须避免在 OneNote 未经用户授权启动时悄然激活 GUI 或重放 mutation。
5. **明确可观察状态。** 后续设计可区分 PowerShell host generation 与同一 host 内 COM instance 的刷新次数；是否投影到 content-free audit/health 需要以最小契约为准，不能记录参数、XML、路径、OneNote ID 或 response 内容。

## 风险与待决问题

- `launch_onenote_gui` 的 GUI-ready 证据是否足以代表新的 COM activation 已可用；若不足，刷新失败应如何在不误报 launch 成功的前提下投影；
- 原 `$onenote` 已断连时的 release/recreate 顺序、异常处理及 host 存活性；
- 刷新控制请求与普通 request 的 framing、sequence、single-flight 和 audit 边界，及其是否应作为独立 protocol kind；
- 断连发生在业务 operation 中时，必须保留 `possibly_dispatched`/`indeterminate` 的既有语义，不能因为后续 refresh 成功而降格；
- one-shot adapter、尚未 lazy-start 的 persistent adapter、CLOSING/CLOSED client 和 host 已被 poison 的情形应分别保持什么 no-op/拒绝/重建行为；
- 是否需要新增 typed error/recovery action，以及它与现有 GUI preflight、`launch_onenote_gui` 和错误分类之间的边界。

## 预期验证

- 确定性 fake-host 合同：同一 host 内刷新后只使用新的模拟 COM instance；host generation、sequence 与单飞约束保持正确；
- 确定性负向合同：刷新失败、控制帧 timeout/EOF、client 已关闭、host 已 poison 等情形均 fail closed，且不重放此前或当前业务 request；
- 控制面合同：未授权、GUI 未 ready、launch timeout 或启动失败时不得触发刷新；one-shot 与未启动的 persistent client 不引入额外 GUI/COM activation；
- 审计合同：新增状态如被记录，必须 content-free，且不改变公开 tool 的业务结果契约；
- 人工验证由用户在 disposable 本地 OneNote 环境中执行：使已有 persistent client 建立后关闭 OneNote，经 `launch_onenote_gui` 恢复，再验证后续只读调用和一个受 policy 控制的成功 mutation。Agent、pytest、CI、hook、import、timer、watcher 与 dry-run 不得触发该真实流程。

## 完成定义

- [ ] 已完成并记录“PowerShell host 原地刷新”与“Python host 重建”两种路径的安全性、耦合和恢复语义比较；
- [ ] 已选择最小实现边界，明确 client 契约、控制请求转发、状态投影和失败语义；
- [ ] 自动化 fake-host 合同覆盖 fresh object、断连/刷新失败、无业务重放及 content-free audit；
- [ ] 当前设计文档、TODO 048 与相关公开契约已按最终实现同步；
- [ ] 用户确认 disposable 本地 OneNote 的关闭 → 内部启动 → 后续 read/mutation 恢复证据，且不存在重复 mutation 或非预期 GUI 启动。

## 关联

- [TODO 048](048_persistent_com_client_bridge.md)：当前 client-adapter、generation、delivery state 与 host 故障收尾合同；本项补足 OneNote 进程重启后 stale COM proxy 的恢复问题。
- [TODO 031](031_start_onenote_desktop_tool.md)：受控、最多一次的 OneNote GUI 启动与 readiness 边界。
- [常驻 OneNote COM Client Bridge 状态模型](../design/persistent_com_client_bridge.md)：当前 host generation、pending request 与不重放约束。
- [TODO 025](025_onenote_com_convergence_and_mutation_coordination.md)：COM failure、收敛与 mutation reconciliation 的既有边界。
