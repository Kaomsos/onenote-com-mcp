# 031：启动 OneNote Desktop GUI 的显式工具

> ID：031
> 状态：待办
> 优先级：P1
> 类型：生产 MCP / Windows Desktop 生命周期
> 更新日期：2026-08-14

## 背景

当前 Windows/OneNote 环境的真实 manual-validation 对照表明：scenario 开始前 OneNote Desktop 已运行时，cache working fixture 可以完成 hierarchy 双稳定；OneNote 未运行、由短命 PowerShell/COM client 冷启动时，working Notebook 可能长期只有空 shell，或刚建立的 live ID 随 client 退出而消失。错误模型与证据边界见 [OneNote COM 冷启动 Fixture hierarchy 丢失](../lesson/onenote_com_cold_start_fixture_hierarchy_loss.md)。

当前已先实施 fail-closed 策略：公开 `health_check` 在任何 hierarchy/COM 调用前用原生 Windows 进程与可见窗口探针确认 OneNote GUI 已启动；manual-validation 单项与真实 `all` 也在创建/打开 working Notebook 前应用同一门限。这个策略可以避免错误扩散，但需要用户自行启动 OneNote。

本 TODO 规划一个显式 `start_onenote_app` MCP 工具：仅当 OneNote GUI 尚未就绪时启动 OneNote Desktop，并在返回成功前确认同一 readiness 条件。它不是长期 COM owner，也不改变每次 bridge 调用独立 PowerShell/COM client 的现有架构。

## 目标合同

- 默认 profile 注册无参数工具 `start_onenote_app`；调用行为是显式的，`health_check` 本身仍只检查、绝不隐式启动。
- 首先执行与 `health_check` 相同的 native readiness probe：
  - 已有 `ONENOTE.EXE` 且存在可见顶层 GUI 时不重复启动，返回 `status="already_running"`；
  - 未就绪时只启动一次受信任的本机 OneNote Desktop executable，并有界等待进程与可见 GUI 同时成立；
  - `ONENOTE.EXE` 进程存在但没有可见 GUI 时不得直接宣称成功，应尝试受支持的显式 GUI 激活路线或返回可操作的 typed failure。
- 成功 envelope 至少返回 content-free 的 `status=started|already_running`、`onenote_desktop={process_running,visible_window_present,ready,probe}` 与启动/收敛耗时；不得返回窗口标题、用户 Notebook、对象 ID、命令行或用户路径。
- 启动失败、可执行文件解析失败、超时和 GUI 未出现使用稳定 typed error，标记 `after_user_action` 或安全的显式重试语义；不得 fallback 到创建 `OneNote.Application` COM 对象并把 COM 可访问误当成 GUI ready。
- 工具只负责启动/显示应用，不打开任意用户提供的 Notebook 路径、不执行 OneNote 内容 mutation、不接受 executable/path/arguments 参数。

## 可执行文件与启动边界

- 从 Windows 注册的 `OneNote.Application` LocalServer 或同等受信任、精确的 Office 安装注册信息解析 executable；必须验证目标是普通绝对本地文件、文件名为 `ONENOTE.EXE`，并拒绝未引用参数、脚本、reparse/相对路径或调用方输入。
- 启动使用参数数组而非 shell command，不经 `cmd.exe`/PowerShell 字符串插值；不得传入 Notebook path、URI、Page 内容或其他可变 payload。
- 一次 tool 调用最多创建一个 OneNote 进程启动请求。若启动 API 返回后状态不确定，只轮询只读 native readiness，不重复发起启动。
- 工具不结束、重启、关闭或接管既有 OneNote 进程，也不自动关闭 modal dialog。

## 非目标

- 不实现 scenario-scoped 或生产级长期 COM owner、COM broker、后台 daemon、watcher 或跨 MCP 进程 session。
- 不使 `health_check`、server import、MCP initialize、tool discovery 或 manual-validation dry-run 隐式启动 GUI。
- 不改变 fixture cache template、working copy、checkpoint、双稳定、内容验证和 fail-closed 门限。
- 不承诺 Windows 登录前、非交互 session、服务账户、远程无桌面 session 或所有 OneNote/Office 版本均可启动可见 GUI。

## 自动化验证

- 所有测试 mock 进程枚举、窗口枚举、注册表解析与 process launch；pytest 绝不启动或关闭真实 OneNote。
- 已运行 GUI 路径返回 `already_running`，launch 调用次数为零。
- 未运行路径只发起一次精确 executable launch，并要求连续或有界的 ready observation 后才返回 `started`。
- process-only/no-window、launch exception、注册表目标异常、timeout、探针失败均返回稳定非成功 envelope；不发生 COM、Notebook 或文件 mutation。
- Tool schema 无参数且禁止任意 path/argument；default profile 工具数、README、`tool_contracts.md` 与 `health_check` capability 同步。
- manual-validation pure tests 证明 dry-run 零 GUI probe/launch，真实 run 的当前 fail-closed preflight 不因工具存在而自动调用它。
- 完整 `pytest -q`、相关 dry-run 和 `git diff --check` 通过。

## 真实验证

只有用户可以执行涉及真实 OneNote Desktop 的验收：

1. 完全退出 OneNote，调用 `health_check`，确认以 `onenote_desktop_not_running` fail closed 且没有隐式启动应用；
2. 调用 `start_onenote_app`，确认只出现一个可见 OneNote GUI，返回 `started`；
3. 再次调用，确认返回 `already_running` 且不产生第二次启动；
4. 调用 `health_check`，确认 `onenote_desktop.ready=true` 后才进行 hierarchy COM 读取；
5. 用户随后运行一个 cache manual-validation 单项，确认 fixture 可以进入 hierarchy/content validation；这只验证启动工具与当前环境的配合，不关闭长期 COM owner 议题。

## 完成定义

- 工具实现、typed errors、无参数 schema、可信 executable 解析、single-launch convergence 和 content-free response 全部有纯合同覆盖；
- 生产/README/design/manual-validation 文档同步，`health_check` 保持 check-only；
- 完整自动化测试通过且没有真实 OneNote side effect；
- 用户完成上述真实启动、幂等、健康检查和至少一个 cache consumer 验证，并确认没有重复 GUI 或残留 helper 进程。

## 关联

- [当前架构](../design/architecture.md#6-运行时生命周期与并发)
- [公开工具合同](../design/tool_contracts.md)
- [冷启动错误模型 Lesson](../lesson/onenote_com_cold_start_fixture_hierarchy_loss.md)
- [Manual Validation Cache 激活 TODO 030](030_manual_validation_cache_hierarchy_activation_batching.md)
