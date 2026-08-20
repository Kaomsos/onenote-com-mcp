# 048：常驻 OneNote COM Client Bridge

> ID：048
> 状态：待办
> 优先级：P0
> 类型：OneNote COM / Bridge Transport / 性能 / Mutation 安全
> 更新日期：2026-08-20

## 决策摘要

当前每次 `OneNoteBridge.call()` 都以 `subprocess.run()` 启动新的 `powershell.exe`、传入固定 bridge 脚本、在该进程中创建 `OneNote.Application`，并使用本次调用的临时 JSON 请求/响应文件。一次 tool call 的每个 backend call 因而重复承担 PowerShell 进程启动、脚本解析、COM activation 与临时文件 I/O。

本项不再绑定 `pywin32` 或 Python 进程内 COM。目标是建立受控的常驻 COM client 边界：每个 MCP server 进程只启用一个选定 adapter，由 adapter 的单一受控执行 owner 创建、串行使用、释放并在故障后替换一个 `OneNote.Application` client。Runtime、service 与公开 tool 只调用统一 bridge，不取得或跨线程/跨进程传递 COM proxy。

默认目标实现是常驻 Windows PowerShell STA host：host 启动后一次创建 `$onenote = New-Object -ComObject OneNote.Application`，以固定 JSON request/response protocol 串行处理调用，并复用现有 PowerShell operation allowlist、`[ref]` out 参数适配与 HRESULT 投影。当前 one-shot PowerShell bridge 仍是生产基线；只有完成本 TODO 的实现与验证后，常驻 PowerShell host 才成为默认 production adapter。

未来 adapter（包括 pywin32）可以接入该边界，但必须独立满足相同的 operation、错误、安全、生命周期和验证契约。它们不能成为默认 PowerShell adapter 的运行依赖，也不能改变公开 tool 权限或结果语义。

## 2026-08-19 阻塞证据：当前环境的 pywin32 名称解析不可用

本项的首次实现已回滚。用户在 disposable 本地验证 run
`run-2026-08-19-23-26-58` 和 `run-2026-08-19-23-27-14` 中稳定复现：

- 两次都在 `create-source-notebook` 的第一个 lifecycle `get_hierarchy` 失败；尚未启动 scenario MCP、未创建 Notebook、未执行 mutation；
- lifecycle bridge audit 将失败投影为 `OneNoteBridgeError`，其 leaf exception type 为 `AttributeError`；
- 用户随后以相同 STA、只读 probe 分别测试 `win32com.client.Dispatch` 与 `win32com.client.dynamic.Dispatch`。两者均得到动态 `CDispatch`，且原始 `IDispatch.GetIDsOfNames(0, "GetHierarchy")` 均返回 `0x8002801D`（`TYPE_E_LIBNOTREGISTERED`）。

因此失败发生在 pywin32 从固定方法名解析 DISP ID 的阶段，而不是 Notebook 路径、cache、worker 并发、BYREF out 参数、timeout 或业务 mutation。强制 `dynamic.Dispatch` 没有改变结果；`makepy`、`EnsureDispatch` 和预生成 binding 更依赖 type library，不能作为当前阻塞的规避方式。

这只是当前 OneNote 安装/进程环境的 pywin32 兼容性结论，不宣称所有 OneNote 安装都不能使用 pywin32。2026-08-20 的校正探针确认，同一环境中的 Windows PowerShell COM binder 与生产 one-shot bridge 均可正常调用 `GetHierarchy`；当前 blocker 已收窄为 pywin32 动态名称解析路径，而不是 OneNote COM 或 PowerShell `[ref]` 的普遍不可用。

## 2026-08-20 校正证据：PowerShell COM client 可调用并在进程内复用

早期补充 probe 错把 `HierarchyScope.hsPages = 4` 当成 XML schema，执行了 `$onenote.GetHierarchy("", 2, [ref]$xml, 4)`，随后又以同样错误的 `schema=4` 调用生产 `OneNoteBridge`。仓库生产常量实际为 `XML_SCHEMA_2013 = 2`；因此这两次 `PSInvalidCastException` / `E_NOINTERFACE` 失败只能证明错误参数组合不可调用，不能作为 PowerShell transport、OneNote type library 或 `[ref]` 适配失败的证据。该错误结论现已撤销。

用户随后在已打开的可见 OneNote Desktop GUI 上运行校正后的只读 [`powershell-com-dispath-smoke-v0.ps1`](../../scripts/powershell-com-dispath-smoke-v0.ps1)。Probe 固定使用 `HierarchyScope.hsNotebooks = 2` 与 `XMLSchema.xs2013 = 2`，不创建、打开、更新、删除、关闭或同步 Notebook，不把 hierarchy XML 放入 response、日志或文件：

- Windows PowerShell `5.1.26100.9168`、64-bit、STA 成功创建一个 `OneNote.Application` COM client；
- 单次校正调用完成，`GetHierarchy` COM invocation 耗时 `18.367 ms`，随后 COM RCW 成功释放；
- 扩展 probe 在同一个 PowerShell host 中只创建一次 COM client，把两个固定的 content-free request 分别序列化为 JSON、逐条反序列化与 allowlist dispatch，再通过同一 client 串行执行两次 `GetHierarchy`；
- 最终证据为 `request_count=2`、`response_count=2`、request/response ID 顺序一致、`responses_correlated=true`、`max_concurrent_com_calls=1`、`completed_invocations=2`、`com_client_creation_count=1`、`com_client_reused=true`；两次 invocation 分别耗时 `13.481 ms` 与 `6.861 ms`，probe 内 JSON loop 总耗时 `66.54 ms`，最终 RCW 成功释放；
- 生产 debug trace `session-20260820T035301-26764-542676ee.jsonl` 还记录了 `query_notebook`、`query_page` 与 `list_notebooks` 三个成功终态，合计四次 `get_hierarchy` backend dispatch，全部 `observed_outcome=completed` 且 `replayed=false`。这与校正 probe 一致，确认当前 one-shot PowerShell bridge 在该环境可用。

这项真实只读证据证明：PowerShell binder 的 `GetHierarchy`/`[ref]` 路径可用，并且单个 PowerShell 进程内的同一 COM client 可以承载多个串行 JSON request。它尚未验证独立常驻 child process 的 stdin/stdout framing、跨 Python `call()` 生命周期、全部 23 个 operation、typed failure、timeout、host 崩溃、shutdown、不重放语义或端到端性能；`18.367 ms`、`13.481 ms` 和 `6.861 ms` 都不包含 production one-shot 的完整进程启动与 COM activation 成本，不能单独作为性能收益结论。

## 2026-08-20 范围决策：客户端无关的常驻 COM 边界

项目决定以常驻 COM client bridge 为目标，并将 Windows PowerShell STA host 定为默认实现。该决定消除了继续寻找 pywin32 名称解析规避方案的前置依赖；当前 pywin32 失败证据保留为 optional adapter 的兼容性限制，而非本项 blocker。

因此状态从“阻塞”改为“待办”。实现仍不得仅凭 fake COM 或本次进程内 smoke 推进到完成：默认 PowerShell adapter 必须满足下述全部生产和真实验证门限；后续任何 client adapter 也必须逐一通过同等门限后才能被显式接入。

## 当前边界与缺口

- `OneNoteBridge.call()` 当前为每个 backend operation 创建并等待一个新的 `powershell.exe`；COM proxy 不能跨该进程保存；
- PowerShell 的 `[ref]` 参数包装了 `GetHierarchy`、`OpenHierarchy` 等 OneNote API 的 out 参数；默认 adapter 必须复用或经逐 operation 合同测试证明等价的适配，而不是把 23 个调用机械复制到另一种语言；
- 当前错误投影、timeout、audit、debug trace backend 行、operation catalog 和 mutation 收敛/对账契约均依赖 bridge 的稳定结果语义；
- FastMCP/Runtime 的调用线程不应直接跨线程或跨进程持有 COM proxy。每种 adapter 都必须由其单一受控 execution owner 管理 apartment、proxy/client 的创建、调用和释放。

## 工作范围

### A. 客户端无关的常驻 COM bridge

1. 定义受控的 client-adapter 契约：adapter 标识、单一 execution owner、client 创建/显式 shutdown、串行 dispatch、故障后的干净重建，以及不向 Runtime、service 或公开 tool 暴露 COM proxy。
2. 实现默认 `persistent_powershell` adapter：一个显式 STA 的 Windows PowerShell host 在自己的进程中创建一个 `$onenote` client；Python bridge 以有界请求队列和受控 request/response framing 向该 host 发送请求，host 内同时只执行一个 operation。
3. 将既有固定 PowerShell operation allowlist、参数验证、`[ref]` out 参数、结果 JSON shape、typed HRESULT/error 投影和 content-free audit/debug trace 语义复用于默认 adapter。参数继续作为数据传递，绝不由用户输入拼接 PowerShell 源代码、命令字符串或日志。
4. 默认 adapter 的 protocol 必须为每个 request 关联唯一、无内容的 sequence/correlation 信息；只输出受控 response，拒绝畸形/未知/重复 request，且不允许 host 的 incidental stdout 破坏 response framing。
5. 后续 adapter 只能通过同一契约注册为显式选择项；`pywin32`、`makepy`、`EnsureDispatch`、预生成 type library binding 或其他依赖均不是默认实现前提。

### B. 生命周期与故障语义

1. 单一 adapter client 内的 COM 调用保持串行；后续 TODO 047 的跨调用 read 调度不得据此假定 OneNote COM 可并行。
2. timeout、host/worker 崩溃、COM 断连或 shutdown 中断必须使当前 operation 按既有 `failed`/`partial`/`indeterminate` 语义结束；任何 operation（包括 read）均不得由 transport 自动重放，mutation 尤其不得重放。
3. 若需要 restart，只能在旧 host/worker 已结束后重新建立干净的 client generation；不能复用不明状态的 proxy/client，也不能声称前一个 mutation 未发生。
4. 迁移期间 current one-shot PowerShell 只可作为显式 fallback；其启用条件、选定 adapter、trace/audit 投影和故障语义必须可观察，且不得静默混用多条路径或掩盖默认 adapter 初始化失败。
5. 保留现有 OneNote Desktop 注册、policy、协调、收敛、缓存 invalidation 与 Copy-before-delete 防线；本项只替换 bridge transport，不改变公开 tool 权限或结果契约。

### C. 证据与验证

1. 使用 fake client/host adapter 覆盖 operation dispatch、BYREF/out 参数、HRESULT/error 分类、worker/host 串行性、ContextVar/trace 关联、shutdown、崩溃和 timeout；所有自动化测试不得连接真实 OneNote。
2. 对既有 PowerShell bridge 的每一种公共 operation 建立或复用等价结果/错误合同测试，重点覆盖 `get_hierarchy`、Page XML、创建/更新/删除、层级打开与 batch partial result。
3. 在 content-free trace 中比较同一确定性 workload 的 backend call 数、adapter/client lifecycle、首次调用与稳态端到端耗时；明确区分本项节省的固定 transport 成本与 TODO 045 节省的业务 readback call 数。
4. 由用户在 disposable 本地 OneNote 数据上执行默认 adapter 的 read、成功 mutation、policy rejection 与 shutdown/restart smoke；超时/崩溃/partial failure 以确定性 fake-host 合同测试为准，不在真实 OneNote 上故意构造不确定 mutation。Agent 不执行真实 scenario。
5. 任何可选 adapter 均须重复以上自动化与用户验证矩阵；未通过时不得改变默认 adapter 或隐式接管请求。

## 非目标与安全边界

- 不将单个 `move_page`/Copy 内的 COM 步骤并行化，不缩短 mutation 从 admission 到 finalize 的独占与收敛窗口；
- 不因为 COM proxy 常驻而绕过 typed ID、mutation policy、source drift、confirmation、reconciliation 或 Copy-before-delete；
- 不引入 Azure、Graph、远程服务、遥测或用户内容落盘；
- 默认 PowerShell adapter 不依赖 OneNote type library 预生成绑定；可选 adapter 的额外先决条件必须显式声明，且不得迫使默认路径注册 type library 或退回不受控的 PowerShell 文本插值；
- 不以 transport timeout 后的“新 client generation 可用”推断前一 mutation 没有发生。

## 完成定义

- [ ] client-adapter 契约已落地，默认 `persistent_powershell` adapter 已实现，且生产 bridge 不再为每次 backend call 启动 `powershell.exe`；
- [ ] 默认 STA PowerShell host 完整拥有 COM client 生命周期、串行 dispatch、显式 shutdown 与故障后的干净重建；后续 adapter 通过同一边界接入，不能暴露或跨 owner 传递 COM proxy；
- [ ] 所有既有 bridge operation 的参数/out 参数、成功/partial/失败结果及 HRESULT 投影与公开 Runtime 契约兼容；
- [ ] 自动化测试证明 host/worker 不跨调用串线、不会泄漏 COM/client/process/thread/queue 资源，timeout/崩溃不会重放任何 operation，且 trace/audit 不含内容或敏感标识；
- [ ] 已记录同 workload 的 transport 性能基线与收益，未将 readback 数量优化误归因于本项；
- [ ] 用户确认默认 adapter 在 disposable 本地 OneNote 场景中的必要 read/mutation/restart 真实证据；如 fallback 或可选 adapter 保留，已验证其选择与故障语义。

## 关联

- [TODO 025](025_onenote_com_convergence_and_mutation_coordination.md)：当前 COM error、收敛、对账与进程内协调基线。
- [TODO 044](044_mcp_runtime_debug_tracing.md)：content-free backend transport 与 per-call trace 证据。
- [TODO 045](045_copy_move_readback_snapshot_efficiency.md)：减少单次 Copy/Move 的业务 readback；与本项的固定 bridge 成本优化互补。
- [TODO 047](047_concurrent_read_tool_execution.md)：跨调用 read 调度；即使实施，本项仍保持单一 COM client execution owner 串行。
- [Operation Runtime](../design/operation_runtime.md)：operation 生命周期、Outcome 与协调契约。
