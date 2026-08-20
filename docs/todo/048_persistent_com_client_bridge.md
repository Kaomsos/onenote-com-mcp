# 048：常驻 OneNote COM Client Bridge

> ID：048
> 状态：已完成
> 优先级：P0
> 类型：OneNote COM / Bridge Transport / 性能 / Mutation 安全
> 更新日期：2026-08-20

## 决策摘要

生产默认 adapter 现为 `persistent_powershell`：`OneNoteBridge` 持有单一 `ComClient`，懒启动一个 STA PowerShell host，并在该 host 内复用一个 `OneNote.Application` client。`one_shot_powershell` 只在 `LOCAL_ONENOTE_BRIDGE_ADAPTER` 显式选择时启用。本项不绑定 `pywin32` 或 Python 进程内 COM。Runtime、service 与公开 tool 只调用统一 `OneNoteBridge.call()`，不取得或跨线程/跨进程传递 COM proxy。

实现、纯合同测试与用户真实回归均已落地，默认 adapter 已合入 `main`。本项不声明未经测量的量化性能收益；OneNote 重启后 stale COM proxy 与 manual-validation cache 嵌套 Section 闪退分别转由 [TODO 051](051_persistent_com_client_restart_refresh.md) 和 [TODO 052](052_nested_section_cache_crash_investigation.md) 跟踪，因此不再阻塞 048 完成。

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

因此状态从“阻塞”改为实施。实现仍不得仅凭 fake COM 或本次进程内 smoke 推进到完成：默认 PowerShell adapter 必须满足下述全部生产和真实验证门限；后续任何 client adapter 也必须逐一通过同等门限后才能被显式接入。

## 2026-08-20 实现与验收状态

- [`com_client.py`](../../src/local_onenote_mcp/com_client.py) 定义 `ComClient` 契约与投递三态：`responded`、`not_submitted`、`possibly_dispatched`。模块不 import settings、services 或 runtime。
- [`powershell_host.py`](../../src/local_onenote_mcp/powershell_host.py) 持有 23 个 operation 的共享 switch；one-shot wrapper 与常驻 host 由固定常量拼装，不插值用户数据。
- 默认 `PersistentPowerShellClient` 使用 `powershell.exe -NoProfile -NonInteractive -Sta -EncodedCommand`、`ONB1` 帧、generation/sequence、单槽 pending、v1 断连判定、帧大小上限，以及 idle/in-flight 两条 `close()` 路径。CLOSED 后 `execute` 稳定失败。host 与 Python 使用同一 encoded/decoded 上限；host 在 COM 前校验 object、字段类型、generation、sequence 与 operation allowlist；Python 对 response 做完整 schema/类型校验，任何异常 poison 当前 generation。`close()`/`_reap()` 在 reader 结束后关闭 stdout 并清空 `_reader_io`。
- `possibly_dispatched` 接入 `idempotent_retry_allowed()` 与 `MutationAttemptExecutor`：即使重新观察到 exact pre-state 也不二次 execute。`delivery_state` 只保留在内部异常和 bridge audit，`public_details()` 不变。
- `settings.py` 只解析 `LOCAL_ONENOTE_BRIDGE_ADAPTER`；非法值 fail-closed。persistent 初始化失败不降级。
- `server.py` `finally` 与 manual-validation 的 lifecycle wrapper、`mcp_stdio_client`、maintenance cleanup 显式关闭自有 bridge。validation child 与 lifecycle 固定同一 adapter；dry-run 打印 `bridge_adapter`。
- 用户持续执行的 disposable manual-validation 回归已经让默认 adapter 承载真实 read、成功 mutation、policy/lifecycle 边界与进程重启后的失败观察；核心链路获得验收，重启恢复缺口由 TODO 051 独立跟踪。
- 同 workload 双 adapter 的量化性能对比不再作为 048 完成门限。本项只确认 one-shot per-call process 已被默认 persistent host 取代，不据此发布具体加速比例；未来如需性能优化，应以独立测量工作跟踪。

对比命令与用户清单见 [OneNote COM Bridge 运行依赖](../dev/onenote_com_bridge_runtime.md)。

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

- [x] client-adapter 契约已落地，默认 `persistent_powershell` adapter 已实现，且生产 bridge 不再为每次 backend call 启动 `powershell.exe`；
- [x] 默认 STA PowerShell host 完整拥有 COM client 生命周期、串行 dispatch、显式 shutdown 与故障后的干净重建；后续 adapter 通过同一边界接入，不能暴露或跨 owner 传递 COM proxy；
- [x] 所有既有 bridge operation 的参数/out 参数、成功/partial/失败结果及 HRESULT 投影与公开 Runtime 契约兼容；
- [x] 自动化测试证明 host/worker 不跨调用串线、不会泄漏 COM/client/process/thread/queue 资源，timeout/崩溃不会重放任何 operation，且 trace/audit 不含内容或敏感标识；
- [x] 已明确不发布未测量的 transport 加速比例，也不把 TODO 045 的 readback 数量优化归因于本项；用户决定不再以双 adapter 性能对比作为 048 完成门限；
- [x] 用户确认默认 adapter 已承载 disposable 本地 OneNote 的真实 read、成功 mutation、policy/lifecycle 与 restart failure 证据；restart 后 stale proxy 的恢复契约由 TODO 051 继续处理，显式 one-shot fallback 的选择与故障语义由自动化合同覆盖。

自动化四项已由全量 pytest 覆盖；真实 OneNote 结论来自用户执行的人工回归，不由 mock 或 `--dry-run` 替代。2026-08-20 用户明确批准默认 persistent host 合入 `main`，并随后确认将 048 标记为完成。该完成状态不表示 TODO 051/052 已解决，也不授权自动重放不确定请求或放宽 manual-validation 的 fresh-only 门限。

## 关联

- [常驻 OneNote COM Client Bridge 状态模型](../design/persistent_com_client_bridge.md)：当前 adapter/client 生命周期、单飞 pending 请求、delivery state、generation 与故障收尾。
- [TODO 025](025_onenote_com_convergence_and_mutation_coordination.md)：当前 COM error、收敛、对账与进程内协调基线。
- [TODO 044](044_mcp_runtime_debug_tracing.md)：content-free backend transport 与 per-call trace 证据。
- [TODO 045](045_copy_move_readback_snapshot_efficiency.md)：减少单次 Copy/Move 的业务 readback；与本项的固定 bridge 成本优化互补。
- [TODO 047](047_concurrent_read_tool_execution.md)：跨调用 read 调度；即使实施，本项仍保持单一 COM client execution owner 串行。
- [Operation Runtime](../design/operation_runtime.md)：operation 生命周期、Outcome 与协调契约。
