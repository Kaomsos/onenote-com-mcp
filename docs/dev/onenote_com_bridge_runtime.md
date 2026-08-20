# OneNote COM Bridge 运行依赖

> 状态：当前生产行为与开发排障基线
> 更新日期：2026-08-20

## Windows PowerShell host

当前生产 [`OneNoteBridge`](../../src/local_onenote_mcp/bridge.py) 仅支持 Windows。默认 adapter 是 `persistent_powershell`，固定启动：

```text
powershell.exe -NoProfile -NonInteractive -Sta -EncodedCommand <UTF-16LE Base64>
```

这里的 `powershell.exe` 指 Windows PowerShell 5.1。当前生产代码不调用 PowerShell 7 的 `pwsh`，开发、诊断和兼容性结论也不得把 `pwsh` 当作等价 host。默认 host 必须显式建立并验证 STA 所有权；COM proxy 只属于该 host 进程，不能跨线程或跨进程传递。

默认 host 在进程内只创建一次 `OneNote.Application`，随后通过 stdin/stdout 上的 `ONB1` + UTF-8 JSON Base64 帧串行处理请求。参数始终作为数据传递，绝不插值到 PowerShell 源代码或命令字符串。`one_shot_powershell` 只在环境变量 `LOCAL_ONENOTE_BRIDGE_ADAPTER` 显式选择时启用，才会为每次 backend operation 启动新的 `powershell.exe` 并使用临时 JSON 文件；默认 adapter 初始化失败必须 fail-closed，不得静默降级。状态机与投递语义见 [常驻 OneNote COM Client Bridge](../design/persistent_com_client_bridge.md)。

## OneNote COM XML schema

生产代码统一使用 [`XML_SCHEMA_2013 = 2`](../../src/local_onenote_mcp/constants.py)。这是 OneNote COM 的 `XMLSchema.xs2013` 数值，不是 hierarchy scope，也不是用户可配置项。

`GetHierarchy` 的 scope 与 schema 是两个独立参数：

| 用途 | `scope` | `schema` |
| --- | ---: | ---: |
| 只枚举 Notebook（`HierarchyScope.hsNotebooks`） | `2` | `2` |
| 读取到 Page 层级（`HierarchyScope.hsPages`） | `4` | `2` |

正确的只读 PowerShell 调用示例：

```powershell
$xml = ""
$onenote.GetHierarchy("", 2, [ref]$xml, 2)
```

不要把 `HierarchyScope.hsPages = 4` 传到第四个 `schema` 参数。`GetHierarchy("", 2, [ref]$xml, 4)` 不是生产调用，也不能作为 COM 注册、PowerShell binder 或 `[ref]` 兼容性证据。

服务层必须复用 `XML_SCHEMA_2013`，不得在各 operation 中散落 schema 字面量、接受用户提供的 schema，或在失败后猜测其他 schema 重试。Scope 仍由类型化 operation 按 `HIERARCHY_SCOPES` 选择。

## 只读诊断基线

仓库的 [`powershell-com-dispath-smoke-v0.ps1`](../../scripts/powershell-com-dispath-smoke-v0.ps1) 使用 Windows PowerShell 5.1、STA、`scope=2` 与 `schema=2`，只读验证：

- 一个 `OneNote.Application` COM client 能完成 `GetHierarchy`；
- 同一个 client 能串行处理两个 content-free JSON request；
- response 不包含或持久化 hierarchy XML；
- 最终显式释放 COM RCW。

该 smoke 只证明当前机器上的只读调用与进程内 client 复用可行，不证明独立常驻 child process、stdin/stdout framing、全部 operation、timeout、崩溃、shutdown、不重放语义或端到端性能。真实 mutation 验证仍只能由用户通过具名 manual-validation scenario 显式启动。

## Adapter 选择

`settings.py` 只解析 `LOCAL_ONENOTE_BRIDGE_ADAPTER` 名称，非法值 fail-closed。`OneNoteBridge` 按名称装配具体 client。

| 值 | 行为 |
| --- | --- |
| 未设置 / `persistent_powershell` | 默认。懒启动一个 STA host，复用单一 `OneNote.Application`。 |
| `one_shot_powershell` | 显式 fallback。每次 backend call 启动新的 `powershell.exe` 并使用临时 JSON 文件。 |
| 其他 | 启动期失败，不静默降级。 |

`import`、`health_check` 与 `launch_onenote_gui` 不 spawn host。manual-validation 的 scenario MCP child 与 lifecycle wrapper 固定写入同一显式 adapter，不继承父进程环境变量；dry-run 计划输出 `bridge_adapter`。

## 同 workload 双 adapter 对比（content-free）

本项只改变每次 backend call 的固定 transport 成本，不减少业务 readback 次数。对比时必须使用同一确定性只读 workload，并区分 TODO 045 的 snapshot/readback 优化。

1. 启动并保留可见 OneNote Desktop GUI。
2. 两次独立 MCP 进程分别使用默认 adapter 与 `LOCAL_ONENOTE_BRIDGE_ADAPTER=one_shot_powershell`。每个进程同时启用 `LOCAL_ONENOTE_MCP_DEBUG_TRACE=true`，并设置互不重叠的 `LOCAL_ONENOTE_BRIDGE_AUDIT_PATH`。`adapter`、`client_generation` 与 `delivery_state` 只写入 bridge audit，不会出现在 debug trace。
3. 每个进程执行相同的只读序列：`health_check`，再连续两次 `list_notebooks` 或 `query_notebook`。
4. 从 debug trace 比较 backend call 数、首次调用耗时与第二次稳态耗时；从对应 audit JSONL 比较 `adapter`、`client_generation`、`delivery_state`。backend call 数应相同；差异只应出现在 transport 耗时和 `client_generation` 是否跨调用保持。
5. 不得把 Copy/Move readback 次数变化记为本项收益。结论由用户运行后写入 [TODO 048](../todo/048_persistent_com_client_bridge.md)。

Agent 不执行真实 scenario 或真实 MCP 读/写。

## 用户真实验证命令

Agent、pytest、CI 与后台任务只能运行带 `--dry-run` 的命令。下列真实命令必须由用户本人在交互式前台终端启动，且 OneNote Desktop GUI 必须已经可见：

```powershell
# 静态计划（可含最终 adapter）
.venv\Scripts\python.exe tests\manual_validation\run.py query --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --json

# 默认 adapter：只读
.venv\Scripts\python.exe tests\manual_validation\run.py query

# 默认 adapter：成功 mutation
.venv\Scripts\python.exe tests\manual_validation\run.py rename

# policy 拒绝 + shutdown/restart smoke（独立 GUI 入口；不要由 Agent 启动）
.venv\Scripts\python.exe tests\manual_validation\launch_onenote_gui_check.py --dry-run --json
.venv\Scripts\python.exe tests\manual_validation\launch_onenote_gui_check.py --verbosity verbose
```

显式 fallback 不走 `run.py`（validation child 固定 `persistent_powershell`）。在交互式 MCP 中设置 `LOCAL_ONENOTE_BRIDGE_ADAPTER=one_shot_powershell`，确认 bridge audit 的 `adapter` 字段，以及非法值使服务器启动 fail-closed。debug trace 不含 `adapter`。

## 变更纪律

修改 PowerShell host、启动参数、COM apartment、XML schema 或 bridge transport 时，必须同步更新实现、纯合同测试、当前设计文档、本开发说明和公开 developer guide。不得以另一个 PowerShell edition、错误 schema probe、mock 或 dry-run 结果替代真实 OneNote 兼容性证据。
