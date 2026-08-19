# 044：MCP Runtime 本地 Debug Trace 与工具调用埋点

> ID：044
> 状态：进行中
> 优先级：P1
> 类型：功能 / Operation Runtime / 可观测性 / Debug / 本地文件
> 更新日期：2026-08-19

## 决策摘要

为 MCP 运行时增加默认关闭、仅写本地的工具调用埋点。通过环境变量分别控制开关和输出目录，记录一个公开 tool call 从 Runtime 接收、准入与执行，到响应或异常完成的处理过程，用于定位“请求是否到达、停在哪个阶段、调用了多少次 backend、最终如何结束”等问题。

该能力不是遥测，不进行网络上传，也不改变任何 mutation 权限。Debug trace 必须继续遵守项目现有 content-free 边界：不得记录 OneNote 正文、raw XML、binary、secret、原始 tool 参数/结果、对象 ID 或完整用户路径。

## 实施偏差（2026-08-19）

- **`rejected` 事件**：第一版不定义。FastMCP schema rejection 到不了 Runtime；policy 拒绝已有 `tool_call.authorization_rejected`。未来 transport 层可观测 rejection 时再增加。
- **参数形状投影**：只记录键集合、值类型名、集合长度、是否 `None`；不声称记录“调用者是否提供 optional field”（tool 适配层会透传默认值，该信息在 Runtime 插入点不可观测）。
- **`tool_call.entered` 时点**：表示 Runtime 已接收并成功 resolve 到注册 `OperationSpec` 的公开调用（非 transport 层最早点）。
- **2026-08-19 审阅修正**：终态 trace 删除 `recommended_action`，`observed_outcome`/`retry_safety` 经 allowlist 投影；`classify_error()` 全分支严格 error type allowlist；Writer 容量与写入同锁、停止路径关闭句柄；`tool_call.finalizing` 在 Strategy finalizer 前发出；`platform_preflight_*` 仅 policy 非 `none` 时记录；`caught()` 恢复 `isinstance(OneNoteError)`。
- **2026-08-19 可读性重塑（未发版，直接替换）**：用 `event` 记录 `tool_call.*` 生命周期；backend 行删除重复 `event`，改为 `operation`（固定内部 bridge / `filesystem:*` 标识）+ 每个 tool call 内从 1 起的 `backend_call_id`；JSONL 按阅读优先级插入顺序落盘，不再 `sort_keys`；累计统计只出现在终态 `summary`；删除记录与 `health_check.debug_trace` 中的 `schema_version`，以及逐行 `runtime_stage`/`backend_calls`/`attempts`/`replayed`/`content_exposed`。不保留兼容 alias、双写或迁移分支。

## 目标环境变量

| 环境变量 | 默认值 | 语义 |
| --- | --- | --- |
| `LOCAL_ONENOTE_MCP_DEBUG_TRACE` | `false` | 是否启用持久 Runtime debug trace；只接受项目统一的严格布尔值。 |
| `LOCAL_ONENOTE_MCP_DEBUG_DIR` | `~/.onenote-mcp/debug-trace` | 可选的 Debug trace 输出根目录。启用 trace 且变量未设置时使用该用户本地默认目录；显式目录必须为绝对普通目录。目录缺失时会尝试创建（含父目录），显式空值、相对路径、reparse point、非目录或无法创建时在服务器启动 fail closed。 |

开关关闭时必须零目录创建、零文件写入，也不得因为配置了输出目录而隐式启用。该开关不属于 Create/Writes/Deletes/Local File IO 等 tool authorization gate，不能授权任何 OneNote 或用户文件 mutation。

## 记录模型

### 调用与事件

每次 tool call 生成一个不可由调用者指定的进程内 correlation ID（`execution_context.py`），以及 session 内单调递增的 `tool_call_id`，并记录以下事件：

1. `tool_call.entered`：Runtime 已接收并成功 resolve 到注册 `OperationSpec` 的公开调用（附 `operation_kind`、`operation_strategy`）；
2. `tool_call.validated`：FastMCP schema 适配后的 registry admission（含参数形状投影）；
3. `tool_call.authorized` 或 `tool_call.authorization_rejected`：Operation Registry 与 policy 判定完成；
4. `tool_call.platform_preflight_started` / `completed` / `failed`：适用的 GUI/readiness 检查；
5. `tool_call.handler_started`：取得协调 lease 后；
6. backend 行：一次 backend 调用已登记并即将发出（`backend_call_id` + 固定内部 `operation` + content-free typed category；无 `event`）；
7. `tool_call.finalizing`：finalize 前；
8. `tool_call.completed`、`tool_call.failed` 或 `tool_call.cancelled`：冻结最终状态、总耗时与 `summary`（每次调用恰好一个终态）。

Tool 行前缀为 `tool_call_id`、`tool`、时间、`event`、`correlation_id`。Backend 行前缀为 `backend_call_id`、`operation`、关联 `tool_call_id`/`tool`、时间。累计 `backend_call_count` / `attempts` / `replayed` 只出现在终态 `summary`。

### 参数与结果边界

- 不保存原始 JSON-RPC/MCP request、tool arguments 或返回 result；
- 参数形状只记录键集合、值类型名、集合长度、是否为 `None`；
- 不保存 Notebook/Page/Section ID、名称、标题、搜索词、正文、HTML、XML、文件路径、Base64、异常 message 或 bridge request/response；
- backend `operation` 只记录固定内部 bridge 操作名或 `filesystem:*` 标识，不记录参数或 payload；
- error 只保存稳定公开 code、error type、partial/indeterminate/retry-safe 布尔投影（经 `classify_error()`）；
- Debug 模式不得放宽现有 Runtime audit、bridge audit 或 MCP response 的脱敏规则。

## 架构要点

- `OperationRuntime.execute()` 以 `with tracer.call(...) as span` 拥有完整 trace 生命周期；`invoke()` 不感知 trace。
- `debug_trace.py` 集中 config、TraceEvent、脱敏投影、JSONL writer、`status()`。
- `TraceSink`/`TraceSpan` Protocol 与 `_NullTraceSink` 在 `operation_runtime.py`；bridge 通过 `execution_context.current_correlation_id()` 可选对账，不 import `debug_trace`。

## 完成定义

- [x] 两个环境变量、严格解析、默认关闭与启动期目录验证已实现；
- [x] Runtime→Service/bridge dispatch→response 的固定事件模型与 correlation ID 已落地；
- [x] JSONL writer 具备独占 session、并发安全、有界容量、flush/close 和稳定失败语义；
- [x] 所有事件通过 content-free 投影，自动化 sentinel 证明无参数、内容、ID、路径或 secret 泄露；
- [x] Runtime outcome、bridge audit（可选 `correlation_id`）、debug trace 可对账；
- [x] 纯自动化合同通过（聚焦 debug trace / bridge audit，以及全量 pytest）；
- [x] 当前设计文档、根 README 和中英文公开配置文档同步完成；
- [ ] 用户确认默认关闭、显式目录和本地-only/no-telemetry 产品边界（本地 smoke）。

## 本地人工 smoke（用户执行）

```powershell
$env:LOCAL_ONENOTE_MCP_DEBUG_TRACE = "true"
$env:LOCAL_ONENOTE_MCP_DEBUG_DIR = "C:\Users\you\local-onenote-debug"
# 重启 MCP 后调用 health_check、一个只读 tool、一个被 policy 拒绝的 mutation tool
```

## 关联

- [Operation Runtime](../design/operation_runtime.md)：Debug trace 权威设计（§9）。
- [总体架构](../design/architecture.md)：新模块依赖方向。
- [工具契约](../design/tool_contracts.md)：`health_check.debug_trace` 投影。
- [TODO 索引](README.md)
