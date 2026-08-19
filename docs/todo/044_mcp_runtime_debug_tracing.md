# 044：MCP Runtime 本地 Debug Trace 与工具调用埋点

> ID：044
> 状态：待办
> 优先级：P1
> 类型：功能 / Operation Runtime / 可观测性 / Debug / 本地文件
> 更新日期：2026-08-19

## 决策摘要

为 MCP 运行时增加默认关闭、仅写本地的工具调用埋点。通过环境变量分别控制开关和输出目录，记录一个公开 tool call 从 transport 接收、Runtime 准入与执行，到响应或异常完成的处理过程，用于定位“请求是否到达、停在哪个阶段、调用了多少次 backend、最终如何结束”等问题。

该能力不是遥测，不进行网络上传，也不改变任何 mutation 权限。Debug trace 必须继续遵守项目现有 content-free 边界：不得记录 OneNote 正文、raw XML、binary、secret、原始 tool 参数/结果、对象 ID 或完整用户路径。

## 当前缺口

- `OperationRuntime` 目前只有固定长度的进程内 content-free audit，进程结束后无法用于复盘；
- `LOCAL_ONENOTE_BRIDGE_AUDIT_PATH` 只记录 PowerShell/COM bridge operation，不覆盖 MCP tool 请求的接收、参数校验、authorization、platform preflight、handler、finalize 和 response 映射；
- MCP 客户端只看到最终成功或错误 envelope，无法判断请求是在 transport、Runtime、Service、bridge 还是 read-back 阶段停滞；
- 当前没有统一的本地 correlation ID 将一次 tool call 的 Runtime phase、backend call count、reconciliation 和最终 outcome 串联起来。

## 目标环境变量

暂定公开配置名如下；实施时若需调整，必须在同一变更中同步配置解析、README、双语公开配置文档和自动化合同：

| 环境变量 | 默认值 | 语义 |
| --- | --- | --- |
| `LOCAL_ONENOTE_MCP_DEBUG_TRACE` | `false` | 是否启用持久 Runtime debug trace；只接受项目统一的严格布尔值。 |
| `LOCAL_ONENOTE_MCP_DEBUG_DIR` | 无 | Debug trace 输出根目录。启用 trace 时必须提供绝对本地目录；缺失、相对路径、不可写或不安全路径在服务器启动时 fail closed。 |

开关关闭时必须零目录创建、零文件写入，也不得因为配置了输出目录而隐式启用。该开关不属于 Create/Writes/Deletes/Local File IO 等 tool authorization gate，不能授权任何 OneNote 或用户文件 mutation。

## 记录模型

### 调用与阶段

每次 tool call 生成一个不可由调用者指定的进程内 correlation ID，并至少记录以下阶段：

1. `received`：transport 已接收公开 tool 名称；
2. `validated` 或 `rejected`：schema/参数形状校验完成；
3. `authorized` 或 `authorization_rejected`：Operation Registry 与 policy 判定完成；
4. `platform_preflight`：适用的 GUI/readiness 检查开始与结束；
5. `handler_started`：取得协调 lease 并进入 operation-specific handler；
6. `backend_progress`：只记录累计 backend call count、operation phase 和 content-free typed category；
7. `finalizing`：read-back、reconciliation、response projection 或异常映射；
8. `completed`、`failed` 或 `cancelled`：冻结最终状态并记录总耗时。

阶段名称必须来自固定 allowlist，不允许 Service 拼接任意字符串。事件需包含 UTC 时间、monotonic elapsed、tool name、operation kind/strategy、阶段、状态、backend call count、mutation attempt/replay 布尔值、稳定错误 code/type、`content_exposed=false` 和 trace schema version。不得把业务 payload 放入通用 `details` 字段。

### 参数与结果边界

- 不保存原始 JSON-RPC/MCP request、tool arguments 或返回 result；
- 可以记录固定 allowlist 的参数形状，例如参数键集合、值类型、集合长度、是否提供 optional field，但不得保存任何值；
- 不保存 Notebook/Page/Section ID、名称、标题、搜索词、正文、HTML、XML、文件路径、Base64、异常 message 或 bridge request/response；
- error 只保存稳定公开 code、经过 allowlist 的 error type、partial/indeterminate/retry-safe 等布尔投影；
- Debug 模式不得放宽现有 Runtime audit、bridge audit 或 MCP response 的脱敏规则。

## 文件与生命周期合同

- 输出仅位于用户显式配置的绝对本地 debug 根下；不得回退到仓库、当前目录、用户 profile、TEMP 或 OneNote Notebook 路径；
- 每个 MCP 进程使用独立、不可冲突的 session 文件，格式优先为带 schema version 的 UTF-8 JSONL；并发写入必须序列化，单条事件一次完整追加；
- 必须有固定的单文件大小、session 文件数量或总字节上限，超限采用可诊断的停止/轮转策略，禁止无界增长；
- 启动时先验证目录 containment、普通目录形状、reparse point、可写性和当前 session 文件独占创建；不得覆盖已有文件；
- 配置错误应在接受 tool call 前 fail closed。运行中 trace 写入失败不得重放、回滚或改变已经发生的 OneNote operation；应停止本 session 的继续落盘，并向 stderr 输出 content-free 的稳定诊断；
- 服务正常退出时 flush/close；异常退出允许保留最后一个完整 JSONL 事件，不要求修复半写文件，也不得因此清理其他 session；
- 不自动上传、打包、删除或清理 debug artifacts。文档必须提醒用户这些本地文件包含操作时序和工具名，分享前仍需审查。

## Runtime 集成边界

- transport adapter 负责创建最早的 request/correlation context，但不得绕过 `OperationRuntime` 调用 Service；
- `OperationRuntime` 负责 canonical phase 和最终 outcome 埋点；Service/bridge 只通过窄接口增加 content-free backend progress，不得各自创建不兼容日志格式；
- 现有内存 audit 保持可用，持久 debug trace 消费同一 allowlist projection，不能形成第二套更宽的数据模型；
- `LOCAL_ONENOTE_BRIDGE_AUDIT_PATH` 保持独立且兼容。若同时启用，两者可通过 Runtime 生成的 content-free correlation ID 对账，但 Runtime trace 不复制 bridge payload，bridge audit 也不升级为 tool 参数日志；
- `health_check` 可以只投影 `debug_trace_enabled`、schema version 和输出目录是否已配置/可写，不返回完整目录路径或文件名。

## 自动化与验证

### 纯自动化合同

- 开关默认关闭，以及关闭时零 filesystem side effect；
- 启用但目录缺失、相对、不可写、reparse、已有同名 session 或超出路径预算时启动失败；
- read、mutation authorization rejection、成功 mutation fake、typed failure、partial、timeout、cancel 和 unexpected exception 的完整阶段序列；
- correlation ID 在单次调用内稳定、并发调用间唯一，事件按各自 monotonic 顺序排列；
- 参数/结果/异常中注入正文、XML、对象 ID、secret、路径和 binary sentinel，证明 trace 与 stderr 均不泄露；
- trace writer 失败、达到容量上限和进程 finalize 时不重放 tool call、不改变 Runtime outcome、不留下未关闭 handle；
- 与现有 `OperationRuntime.audit_events`、backend call counter 和 `LOCAL_ONENOTE_BRIDGE_AUDIT_PATH` 的组合兼容性；
- Windows 路径、文件独占创建、并发追加与 UTF-8 JSONL 解析合同。

### 本地人工 smoke

只需启动一个显式启用 debug trace 的 MCP 进程，调用 `health_check`、一个纯读取 tool 和一个由 policy 拒绝的 mutation tool，确认输出目录、阶段顺序、最终状态和脱敏边界。该 smoke 不要求真实 OneNote mutation；agent/CI 不得为了验证 trace 而扩权或启动真实 mutation scenario。

## 文档与发布要求

- 实现时同步根 README、`docs/design/operation_runtime.md`、`docs/design/architecture.md`、`docs/design/tool_contracts.md` 和中英文公开 configuration/safety 文档；
- 明确保持 “no telemetry / no remote content processing”：本功能是用户显式开启的本地诊断文件；
- 配置示例必须同时展示开关与绝对输出目录，说明重启 MCP 后生效、默认关闭、磁盘容量与敏感元数据审查责任；
- 任何 trace schema 或环境变量名称成为公开版本后，按兼容性契约处理，不得静默改名或改变默认值。

## 非目标

- 不记录或重放完整 MCP/JSON-RPC traffic；
- 不提供远程 collector、OpenTelemetry exporter、云端 dashboard、自动 issue 上传或后台 telemetry；
- 不把 debug trace 用作 mutation 授权、事务日志、恢复日志、跨进程锁或 replay 输入；
- 不记录 Page 内容、raw XML、binary、secret、原始参数/结果、对象 ID 或完整路径；
- 不修改 `.one`/`.onetoc2`，不扫描用户 Notebook 文件，也不自动清理用户指定目录中的既有文件；
- 不承诺 trace 能证明 OneNote Desktop 内部状态，只证明本 MCP 进程观察到的处理阶段。

## 完成定义

- [ ] 两个环境变量、严格解析、默认关闭与启动期目录验证已实现；
- [ ] transport→Runtime→Service/bridge progress→response 的固定阶段 schema 与 correlation ID 已落地；
- [ ] JSONL writer 具备独占 session、并发安全、有界容量、flush/close 和稳定失败语义；
- [ ] 所有事件通过同一 content-free allowlist projection，自动化 sentinel 证明无参数、内容、ID、路径或 secret 泄露；
- [ ] Runtime outcome、bridge audit、debug trace 三者可对账且不形成重复或冲突的执行模型；
- [ ] 纯自动化、Windows 文件合同与本地无 mutation smoke 全部通过；
- [ ] 当前设计文档、根 README 和中英文公开配置/安全文档同步完成；
- [ ] 用户确认默认关闭、显式目录和本地-only/no-telemetry 产品边界。

## 关联

- [Operation Runtime](../design/operation_runtime.md)：当前进程内 audit、phase、backend call counter 与 outcome 的权威设计。
- [总体架构](../design/architecture.md)：Tool adapter、Runtime、Service 与 bridge 的依赖方向。
- [工具契约](../design/tool_contracts.md)：公开配置和 content-free response/audit 边界。
- [TODO 025](025_onenote_com_convergence_and_mutation_coordination.md)：typed error、reconciliation 与 backend progress 的既有基础。
- [TODO 036](036_operation_runtime_control_plane_and_tool_migration.md)：Operation Runtime 控制面落地的历史证据。
- [TODO 索引](README.md)：本条的状态、优先级与摘要必须同步维护。
