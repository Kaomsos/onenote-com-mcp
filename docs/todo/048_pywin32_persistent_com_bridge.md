# 048：pywin32 进程内常驻 OneNote COM Bridge

> ID：048
> 状态：阻塞
> 优先级：P0
> 类型：OneNote COM / Bridge Transport / 性能 / Mutation 安全
> 更新日期：2026-08-19

## 决策摘要

当前每次 `OneNoteBridge.call()` 都以 `subprocess.run()` 启动新的 `powershell.exe`、传入固定 bridge 脚本、在该进程中创建 `OneNote.Application`，并使用本次调用的临时 JSON 请求/响应文件。一次 tool call 的每个 backend call 因而重复承担 PowerShell 进程启动、脚本解析、COM activation 与临时文件 I/O。

本项以 `pywin32` 的动态 IDispatch 客户端替代生产路径的 per-call PowerShell bridge：MCP Python 进程内只有一个专用 STA worker 线程拥有并在其生命周期内复用 `OneNote.Application`。所有 COM 调用仍先在该 worker 中串行执行；这是减少固定 transport 成本的 P0 性能与稳定性工作，不是把 OneNote COM 调用并行化的授权。

现有 bridge 已表明 OneNote type library 在部分机器上可能未可靠注册。因此实现以动态 `win32com.client.Dispatch("OneNote.Application")` 为基线，不将 `makepy`、`EnsureDispatch` 或预生成的 typelib binding 作为运行前提。

## 2026-08-19 阻塞证据：当前环境的 pywin32 名称解析不可用

本项的首次实现已回滚。用户在 disposable 本地验证 run
`run-2026-08-19-23-26-58` 和 `run-2026-08-19-23-27-14` 中稳定复现：

- 两次都在 `create-source-notebook` 的第一个 lifecycle `get_hierarchy` 失败；尚未启动 scenario MCP、未创建 Notebook、未执行 mutation；
- lifecycle bridge audit 将失败投影为 `OneNoteBridgeError`，其 leaf exception type 为 `AttributeError`；
- 用户随后以相同 STA、只读 probe 分别测试 `win32com.client.Dispatch` 与 `win32com.client.dynamic.Dispatch`。两者均得到动态 `CDispatch`，且原始 `IDispatch.GetIDsOfNames(0, "GetHierarchy")` 均返回 `0x8002801D`（`TYPE_E_LIBNOTREGISTERED`）。

因此失败发生在 pywin32 从固定方法名解析 DISP ID 的阶段，而不是 Notebook 路径、cache、worker 并发、BYREF out 参数、timeout 或业务 mutation。强制 `dynamic.Dispatch` 没有改变结果；`makepy`、`EnsureDispatch` 和预生成 binding 更依赖 type library，不能作为当前阻塞的规避方式。

这只是当前 OneNote 安装/进程环境的兼容性结论，不宣称所有 OneNote 安装都不能使用 pywin32。现有 PowerShell transport 在该环境可用，故本项不能以“删除 PowerShell COM 路径且无 fallback”的形态继续推进。

解除阻塞前必须先确定并由用户验证一个兼容策略：要么明确要求并可靠验证可用的 OneNote type library 注册条件，要么采用不依赖该名称解析路径且仍满足 23 个 operation、typed HRESULT、content-free audit、timeout 和不重放契约的 transport。不得仅凭 fake COM 测试恢复实施状态。

## 当前边界与缺口

- `OneNoteBridge.call()` 当前为每个 backend operation 创建并等待一个新的 `powershell.exe`；COM proxy 不能跨该进程保存；
- PowerShell 的 `[ref]` 参数包装了 `GetHierarchy`、`OpenHierarchy` 等 OneNote API 的 out 参数；迁移到 Python 必须显式实现等价的 BYREF/返回值适配，不能假定是机械替换；
- 当前错误投影、timeout、audit、debug trace backend 行、operation catalog 和 mutation 收敛/对账契约均依赖 bridge 的稳定结果语义；
- FastMCP/Runtime 的调用线程不应直接跨线程持有 COM proxy。COM apartment 初始化、proxy 的创建、调用和释放必须由同一受控 worker 管理。

## 工作范围

### A. 受控的 Python COM transport

1. 在 Windows 运行依赖中引入 `pywin32`，并保持非 Windows 的安装/启动失败方式明确且不改变项目 Windows-only 产品边界。
2. 建立单一 `Pywin32OneNoteBridge`：启动专用线程，在该线程执行 `pythoncom.CoInitializeEx(COINIT_APARTMENTTHREADED)`、创建动态 `Dispatch("OneNote.Application")`，并在 shutdown 中释放 proxy 和 `CoUninitialize()`。
3. 以有界请求队列将所有 bridge operation 送入该 worker；不把 COM proxy 暴露给 Runtime、service 或公开 tool，也不在任意 handler 中临时初始化 COM。
4. 将既有 operation allowlist、参数验证、结果 JSON shape、typed HRESULT/error 投影和 content-free audit/debug trace 语义移入该 bridge 边界；参数继续作为数据传递，绝不由用户输入拼接代码或日志。
5. 为 OneNote 的 out 参数建立小型、逐 operation 可测试的 Python 适配层，并将返回值、空值、XML 与 batch partial result 合同与既有 bridge 对齐。

### B. 生命周期与故障语义

1. 单一 worker 内的 COM 调用保持串行；后续 TODO 047 的跨调用 read 调度不得据此假定 OneNote COM 可并行。
2. timeout、worker 崩溃、COM 断连或 shutdown 中断必须使当前 operation 按既有 `failed`/`partial`/`indeterminate` 语义结束；任何 operation（包括 read）均不得由 transport 自动重放，mutation 尤其不得重放。
3. 若需要 restart，只能在已结束的调用之后重新建立干净的 COM worker；不能复用不明状态的 proxy，也不能声称前一个 mutation 未发生。
4. 在迁移期间若保留现有 one-shot PowerShell 路径作为显式 fallback，必须限定其启用条件、trace/audit 投影和故障语义；不得静默混用两条路径或掩盖 pywin32 初始化失败。
5. 保留现有 OneNote Desktop 注册、policy、协调、收敛、缓存 invalidation 与 Copy-before-delete 防线；本项只替换 bridge transport，不改变公开 tool 权限或结果契约。

### C. 证据与验证

1. 使用 fake COM adapter 覆盖 operation dispatch、BYREF/out 参数、HRESULT/error 分类、worker 串行性、ContextVar/trace 关联、shutdown、崩溃和 timeout；所有自动化测试不得连接真实 OneNote。
2. 对既有 PowerShell bridge 的每一种公共 operation 建立或复用等价结果/错误合同测试，重点覆盖 `get_hierarchy`、Page XML、创建/更新/删除、层级打开与 batch partial result。
3. 在 content-free trace 中比较同一确定性 workload 的 backend call 数、bridge worker 生命周期和端到端耗时；明确区分本项节省的固定 transport 成本与 TODO 045 节省的业务 readback call 数。
4. 由用户在 disposable 本地 OneNote 数据上执行 read、成功 mutation、policy rejection、typed COM failure/timeout（如可安全构造）与 shutdown/restart smoke；Agent 不执行真实 scenario。

## 非目标与安全边界

- 不将单个 `move_page`/Copy 内的 COM 步骤并行化，不缩短 mutation 从 admission 到 finalize 的独占与收敛窗口；
- 不因为 COM proxy 常驻而绕过 typed ID、mutation policy、source drift、confirmation、reconciliation 或 Copy-before-delete；
- 不引入 Azure、Graph、远程服务、遥测或用户内容落盘；
- 不依赖 OneNote type library 预生成绑定，也不以缺少 type library 为由退回不受控的 PowerShell 文本插值；
- 不以 transport timeout 后的“新 worker 可用”推断前一 mutation 没有发生。

## 完成定义

- [ ] `pywin32` 作为受控 Windows 依赖已引入，且生产 bridge 不再为每次 backend call 启动 `powershell.exe`；
- [ ] 单一 STA worker 完整拥有 COM 初始化、proxy 生命周期、串行 dispatch、显式 shutdown 与故障后的干净重建；
- [ ] 所有既有 bridge operation 的参数/out 参数、成功/partial/失败结果及 HRESULT 投影与公开 Runtime 契约兼容；
- [ ] 自动化测试证明 worker 不跨调用串线、不会泄漏 COM/thread/queue 资源，timeout/崩溃不会重放任何 operation，且 trace/audit 不含内容或敏感标识；
- [ ] 已记录同 workload 的 transport 性能基线与收益，未将 readback 数量优化误归因于本项；
- [ ] 用户确认在 disposable 本地 OneNote 场景中的必要 read/mutation/restart 真实证据；如 fallback 保留，已验证其选择与故障语义。

## 关联

- [TODO 025](025_onenote_com_convergence_and_mutation_coordination.md)：当前 COM error、收敛、对账与进程内协调基线。
- [TODO 044](044_mcp_runtime_debug_tracing.md)：content-free backend transport 与 per-call trace 证据。
- [TODO 045](045_copy_move_readback_snapshot_efficiency.md)：减少单次 Copy/Move 的业务 readback；与本项的固定 bridge 成本优化互补。
- [TODO 047](047_concurrent_read_tool_execution.md)：跨调用 read 调度；即使实施，本项仍保持单一 COM worker 串行。
- [Operation Runtime](../design/operation_runtime.md)：operation 生命周期、Outcome 与协调契约。
