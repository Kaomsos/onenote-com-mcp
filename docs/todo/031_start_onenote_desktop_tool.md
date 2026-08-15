# 031：显式 `launch_onenote_gui` 工具

> ID：031
> 状态：进行中
> 优先级：P1
> 类型：生产 MCP / Windows Desktop GUI 控制
> 更新日期：2026-08-15

## 当前状态与已确认决策

当前实现仍只有 check-only 的 `health_check`：OneNote Desktop 未运行或没有可见窗口时，读写操作 fail closed，服务器不会隐式启动 GUI。

用户已经授权把本 TODO 的目标工具冻结为 `launch_onenote_gui`，并纳入 [TODO 034](034_pre_user_testing_tool_surface_convergence.md) 的目标 User profile。本文记录目标合同，**不表示工具已经实现或发布**。

已确认：

- 公开名称只使用 `launch_onenote_gui`，不提供 `start_onenote_app` 或其他兼容 alias；
- 工具归入 Session 类，但执行权限归入独立的 UI Control 授权；
- 只有在 OneNote 未启动时才允许发起有限启动尝试；
- 一次调用最多发出一个进程启动请求，随后只做有界、只读的 readiness observation，不重复启动；
- 返回是否成功，以及是本次启动还是原本已经运行；
- `health_check` 继续只检查，绝不因为健康检查、初始化或工具枚举而启动 OneNote。

## 背景

真实 manual-validation 对照表明：OneNote Desktop 已运行时，cache working fixture 可以完成 hierarchy 双稳定；OneNote 未运行、由短命 PowerShell/COM client 冷启动时，working Notebook 可能只形成空 shell，或 live ID 随 client 退出而消失。错误模型与证据边界见 [OneNote COM 冷启动 Fixture hierarchy 丢失](../lesson/onenote_com_cold_start_fixture_hierarchy_loss.md)。

因此启动 GUI 必须是用户可见、显式、可授权的独立操作，不能作为任意 COM 调用的副作用，也不能把“COM 对象可创建”误判为“桌面 GUI 已就绪”。

## 目标公开合同

### Schema

```text
launch_onenote_gui()
```

- 无参数；
- 不接受 executable、path、arguments、Notebook path、OneNote URI 或任意调用方 payload；
- 不允许调用方选择启动程序。

### 行为

1. 使用与 `health_check` 一致的 native readiness probe，判断 `ONENOTE.EXE` 进程和可见顶层窗口是否同时存在。
2. 已就绪时不发起启动，返回成功与 `status="already_running"`。
3. 完全未运行时，解析受信任的 OneNote Desktop executable，一次且仅一次发起 process launch，然后在固定预算内观察 readiness。
4. 进程存在但没有可见窗口时，不得再发起第二次启动。实现只能尝试受支持、无用户 payload 的显式窗口激活，或返回 typed failure。
5. 只有进程和可见 GUI 同时就绪时才返回成功；超时、解析失败、启动异常和只有进程没有窗口都返回稳定失败 envelope。

“有限尝试”特指**一次进程启动请求加有界 readiness 观察**，不是循环创建多个 OneNote 进程。

### 目标响应

成功结果至少包含：

```json
{
  "ok": true,
  "result": {
    "status": "started | already_running",
    "launch_attempted": true,
    "launch_attempts": 1,
    "ready": true
  },
  "warnings": [],
  "execution": {}
}
```

当 `status="already_running"` 时，`launch_attempted=false` 且 `launch_attempts=0`。实际 envelope 还可返回 content-free 的探针类别和有界耗时，但不得返回窗口标题、命令行、用户路径、Notebook、对象 ID 或页面内容。

失败使用 TODO 034 统一的失败 envelope，至少区分：

- trusted executable 无法解析；
- process launch 被拒绝或异常；
- readiness timeout；
- process running but visible GUI unavailable；
- native probe failure。

错误应提供安全的 `after_user_action` 或显式重试提示，不得在失败后自动重复启动。

## 授权与运行时分类

- Exposure：目标 User profile 默认可见；当前尚未注册。
- Category：Session。
- Effect：GUI control，不是普通 read，也不是 OneNote 内容 mutation。
- Authorization：`LOCAL_ONENOTE_ENABLE_UI_CONTROL=true`；默认 false。
- Operation runtime：若工具进入生产 Registry，必须进入 canonical Operation Runtime，并记录 content-free 的 execution metadata。

UI Control 权限同时覆盖 `navigate_to`，但授权一个类别不意味着调用时可以隐式执行另一个工具。`health_check` 不需要 UI Control，因为它保持只读探针。

## 可执行文件与启动边界

- 从 Windows 注册的 `OneNote.Application` LocalServer 或同等受信任、精确的 Office 安装信息解析 executable；
- 目标必须是普通绝对本地文件，文件名为 `ONENOTE.EXE`；拒绝相对路径、脚本、reparse target、未解析命令片段或调用方输入；
- 使用参数数组直接启动，不经 `cmd.exe`、PowerShell 字符串插值或 shell；
- 不通过创建 `OneNote.Application` COM 对象完成冷启动；
- 不结束、重启、关闭或接管既有 OneNote 进程；
- 不自动处理 modal dialog，不打开指定 Notebook/Page，不发送 URI，不执行内容 mutation。

## 非目标

- 不实现长期 COM owner、COM broker、后台 daemon、watcher 或跨 MCP 进程 session；
- 不使 server import、MCP initialize、`tools/list`、`health_check`、manual-validation dry-run 或任何普通工具隐式启动 GUI；
- 不改变 fixture cache、working copy、checkpoint、双稳定和 fail-closed 门限；
- 不承诺在 Windows 登录前、非交互 session、服务账户或无可见桌面的远程 session 中成功；
- 不把“进程已创建”当作“GUI ready”。

## 自动化验证要求

pytest 必须完全 mock 进程枚举、窗口枚举、注册表解析、激活和 process launch，绝不启动或关闭真实 OneNote：

- 已运行且有可见窗口：`already_running`，launch 调用为零；
- 完全未运行：只发起一次精确 executable launch，达到 readiness 后返回 `started`；
- process-only/no-window：不发起第二次 launch；
- launch exception、异常注册目标、timeout、探针失败：稳定非成功 envelope；
- UI Control 默认关闭，拒绝发生在任何启动副作用之前；
- schema 无参数且禁止任意 path/argument；
- `health_check` 在全部路径保持 check-only；
- Registry、README、`tool_contracts.md`、`health_check` capability 与目标 User profile 同步；
- manual-validation dry-run 为零 GUI probe/launch side effect。

## 用户真实验收

只有用户可以执行涉及真实 OneNote Desktop 的验收：

1. 完全退出 OneNote，调用 `health_check`，确认 fail closed 且没有隐式启动；
2. 在 UI Control 未授权时调用 `launch_onenote_gui`，确认在启动前被拒绝；
3. 开启 UI Control 后调用，确认只出现一个可见 OneNote GUI并返回 `started`；
4. 再次调用，确认返回 `already_running`，且没有第二次启动；
5. 调用 `health_check`，确认 `onenote_desktop.ready=true` 后再进行 hierarchy COM 读取；
6. 用户可随后运行一个 cache manual-validation 单项，确认当前 fixture 流程与显式启动后的环境配合正常。

Agent、pytest、CI、hook、timer、watcher 和后台任务都不得执行上述真实启动验收。

## 完成定义

- [x] 用户确认名称、分类、单次启动请求与有界观察语义；
- [x] 用户授权更新本文及 TODO 034 的目标发布方案；
- [ ] 工具实现、trusted executable 解析、single-launch convergence 和 typed errors 完成；
- [ ] 纯合同覆盖完整，且没有真实 OneNote side effect；
- [ ] Registry、README、design、health capability 和 manual-validation 文档同步到已实现状态；
- [ ] 用户完成真实启动、幂等、健康检查和必要的 cache consumer 验收；
- [x] 用户最终批准将 `launch_onenote_gui` 纳入目标 User profile；实际注册仍待实现和验收。

## 关联

- [用户测试前工具面收敛 TODO 034](034_pre_user_testing_tool_surface_convergence.md)
- [当前架构](../design/architecture.md#6-运行时生命周期与并发)
- [公开工具合同](../design/tool_contracts.md)
- [冷启动错误模型 Lesson](../lesson/onenote_com_cold_start_fixture_hierarchy_loss.md)
- [Manual Validation Cache 激活 TODO 030](030_manual_validation_cache_hierarchy_activation_batching.md)
