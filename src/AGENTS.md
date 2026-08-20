# 生产源代码指令

这些规则适用于 `src/` 下的生产代码。

## 架构边界

- `local_onenote_mcp/domain/` 定义类型化 domain object，必须与 MCP transport、子进程执行和 OneNote COM 访问保持独立。
- `page/` 负责 Page 解析、格式化、构建、图片及面向 Copy 的 Page 语义。XML 处理应集中管理，并由 round-trip 或 invariant 测试覆盖。
- `services/` 负责应用编排和 OneNote 操作。它是执行 policy、精确 ID 定位、confirmation field、预算和可恢复失败行为的主要边界。
- `tools/` 将 MCP 输入输出适配到 services。Tool 函数应保持精简、类型化，并与已记录的 response envelope 一致；不要在此重新实现 service 逻辑。
- `bridge.py` 是可信的本地 COM 边界：装配单一 `ComClient`、独占 audit 与错误投影。默认 transport 是常驻 STA PowerShell host 的受控 JSON 帧；`one_shot_powershell` 才使用临时 JSON 文件。绝不能把不可信内容插值到 PowerShell 源代码或命令字符串中。
- `com_client.py` 只定义 adapter 契约与两个具体实现，不得 import settings、services 或 runtime。
- `server.py`、`settings.py` 和 `policy.py` 负责组合与进程级配置。避免环境变量读取散落各处，也不要建立隐藏的替代注册路径。

## 安全与契约规则

- 不得绕过 `MutationPolicy` 检查。如果新 mutation 能力的风险不同于现有写入或删除，必须为其设置独立且 fail-closed 的权限。
- 永久删除、raw XML、实验性 Reparent、Copy 和 Move 必须保持可独立审查，并默认关闭。
- 在支持的地方，mutation 使用精确 object ID 加当前 confirmation field。不得静默回退到名称匹配或宽泛目标。
- Search 和 Copy 工作必须受其配置预算约束。预算耗尽是显式失败，不代表可以继续无界执行。
- 绝不能直接编辑 `.one` 文件，也不能为 OneNote 内容增加云端或网络路径。
- 不得记录 OneNote 内容、bridge payload、secret 或原始 tool 参数。Audit 应保持 content-free。

## 变更与验证

- 公开 tool 契约变化时，需要补充相关测试，并同步更新 `docs/design/`、根 README 内容以及受到影响的验证工作流。
- 新增或修改非只读 tool 时，还必须在 `tests/manual_validation/` 下提供隔离的具名 scenario；遵循该目录的 `AGENTS.md`，并将真实执行留给用户。
- 根据适用情况，为成功、policy 拒绝、畸形输入、bridge 失败以及部分 mutation/restore 行为添加聚焦的单元测试或合同测试。
- 共享生产行为发生变化时，先运行相关测试模块，并在交付前运行完整 pytest 测试集。
