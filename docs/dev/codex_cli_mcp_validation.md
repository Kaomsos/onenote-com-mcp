# 通过 Codex CLI 间接调用本地 MCP 的隔离验证

> 状态：已在 Windows + Codex CLI `0.144.6` 实测
> 更新日期：2026-08-04
> 适用范围：专用、可丢弃的 OneNote 隔离 Notebook

本文记录一种用户显式触发的验证方式：由 `codex exec` 作为一次性编排器，只注册本仓库的 `local-onenote` MCP，并通过阶段性工具白名单完成只读检查或真实 COM mutation。

这不是 CI、后台任务或默认测试。每次运行都必须由用户主动发起，并在开始前确认目标 Notebook 无业务数据且可丢弃。完整场景仍以 [isolated_mutation_validation.md](isolated_mutation_validation.md) 为准。

## 1. 适用性与数据边界

此方法具有两条不同的数据路径：

1. OneNote MCP server、PowerShell 和 COM 均在本机运行；
2. Codex 模型会接收 prompt、MCP tool schema、调用参数以及 MCP 返回的 Notebook 名称、对象 ID、层级和专用测试内容。

因此，运行前必须明确授权将隔离 Notebook 的这些信息发送给 Codex 服务。不得把真实业务 Notebook、个人笔记或秘密信息放入此流程。

若不希望模型接触任何 OneNote 元数据，应改用计划中的本地程序化 runner，见 [TODO 001](../todo/001_programmatic_isolated_mutation_runner.md)。

## 2. 核心原则

- 使用 `--ephemeral`，不保存 Codex session；
- 使用 `--ignore-user-config`，不加载用户全局 MCP 配置；
- 显式禁用 `apps/plugins/remote_plugin`，避免无关连接器启动；
- 命令行完整声明唯一 MCP server，不能假设项目配置一定被 CLI 自动加载；
- 每个阶段设置最小 `enabled_tools`，不把 43 个默认工具全部开放；
- 仅对白名单工具设置 `default_tools_approval_mode="approve"`，解决非交互模式无法回答首次 MCP 审批的问题；
- 禁止使用 `--dangerously-bypass-approvals-and-sandbox`；
- 权限环境变量只传给本次 MCP 子进程，不修改全局或项目配置；
- Delete、永久 Delete 和 raw XML 默认始终关闭；
- 任何身份歧义、策略不符、重复对象或回读不一致都必须立即停止。

## 3. 临时 MCP 配置模板

PowerShell 示例：

```powershell
$repo = "E:\code\MCP\local-onenote-mcp"
$python = "$repo\.venv\Scripts\python.exe"
$mcpTemp = "$repo\.codex-tmp"
$pythonToml = $python.Replace("\", "/")
$mcpTempToml = $mcpTemp.Replace("\", "/")

New-Item -ItemType Directory -Force -Path $mcpTemp | Out-Null

codex -a never exec `
  --ephemeral `
  --ignore-user-config `
  --strict-config `
  --disable apps `
  --disable plugins `
  --disable remote_plugin `
  -m "<available-model>" `
  -s workspace-write `
  -C $repo `
  -c "mcp_servers.local-onenote.command=`"$pythonToml`"" `
  -c 'mcp_servers.local-onenote.args=["-m","local_onenote_mcp.server"]' `
  -c 'mcp_servers.local-onenote.startup_timeout_sec=120' `
  -c 'mcp_servers.local-onenote.tool_timeout_sec=180' `
  -c 'mcp_servers.local-onenote.enabled_tools=["health_check","list_notebooks","resolve_identifier"]' `
  -c 'mcp_servers.local-onenote.default_tools_approval_mode="approve"' `
  -c "mcp_servers.local-onenote.env.TEMP=`"$mcpTempToml`"" `
  -c "mcp_servers.local-onenote.env.TMP=`"$mcpTempToml`"" `
  -c 'mcp_servers.local-onenote.env.LOCAL_ONENOTE_ENABLE_WRITES="true"' `
  -c 'mcp_servers.local-onenote.env.LOCAL_ONENOTE_ENABLE_DELETES="false"' `
  -c 'mcp_servers.local-onenote.env.LOCAL_ONENOTE_ENABLE_PERMANENT_DELETES="false"' `
  -c 'mcp_servers.local-onenote.env.LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_MOVE_SECTION="true"' `
  -c 'mcp_servers.local-onenote.env.LOCAL_ONENOTE_ENABLE_RAW_XML="false"' `
  '<stage-specific prompt>'
```

`<available-model>` 使用当前账户实际可用的 Codex model。模型名称不是此验证契约的一部分。

### 为什么直接使用项目 Python

MCP command 使用 `.venv\Scripts\python.exe -m local_onenote_mcp.server`，而不是再次执行 `uv run`。这样可以：

- 避免验证过程中解析或下载依赖；
- 确保使用当前工作区和已验证虚拟环境；
- 缩短 MCP 启动链，减少故障层次。

## 4. 工具白名单分阶段设计

| 阶段 | 建议 `enabled_tools` | 权限 |
| --- | --- | --- |
| 身份探测 | `health_check,list_notebooks,resolve_identifier` | 只读 |
| 容器准备 | `health_check,get_tree,list_section_groups,list_sections,create_section_group,create_section` | Write |
| Page 准备 | `health_check,list_pages,get_page_text,create_page,reorder_page,get_tree` | Write |
| 图片准备 | `health_check,get_page,get_page_text,get_page_objects,add_image_to_page` | Write |
| 基线记录 | `health_check,get_tree,list_sections,list_pages,get_page_xml,get_page_objects` | 只读 |
| Rename 验证 | 基线工具 + `rename_section_group,rename_section` | Write |
| Reorder 验证 | 基线工具 + `reorder_page` | Write |
| Move 验证 | 基线工具 + `move_section` | Write + Experimental Move |
| 非永久 Delete | 必要查询工具 + typed delete 工具 | Delete；单独进程 |

不要为了简化命令而把 `delete_hierarchy`、raw XML 工具或永久删除工具加入通用白名单。

## 5. Prompt 合同

每个非交互 prompt 至少必须包含：

1. 唯一目标 Notebook/Section/Page 的精确 ID；
2. 允许调用的 MCP server 和工具范围；
3. 预期权限状态；
4. mutation 前的名称、父级、Section、modified 等确认字段；
5. 操作后必须执行的只读回读；
6. 发现重复、类型不符或身份歧义时立即停止；
7. 明确禁止的操作；
8. 机器可读的最终 JSON 字段。

推荐要求最终输出：

```json
{
  "ok": true,
  "target_ids": {},
  "before": {},
  "after": {},
  "verified": {},
  "error": null
}
```

模型的自然语言判断不能代替 MCP 的 read-back 结果。最终验收必须依据 tool 返回的 ID、父级、顺序、Page level、对象列表和内容摘要。

## 6. 已验证的配置问题

### 6.1 只覆盖 env 会出现 `invalid transport`

如果 CLI 没有加载到既有 server 定义，而命令行只传：

```text
mcp_servers.local-onenote.env.LOCAL_ONENOTE_ENABLE_WRITES=true
```

得到的配置只有 `env`，缺少 `command/args`，加载时会报 `invalid transport`。临时调用必须完整传入 command、args、timeout 和 env。

### 6.2 MCP/COM 读取也需要可写临时目录

Python MCP stdio client 和 `OneNoteBridge` 都需要临时文件或 Windows named pipe。受限 Codex 沙箱可能无法使用 `%TEMP%`，表现为：

```text
FileNotFoundError: No usable temporary directory found
```

解决方式是创建专用工作区临时目录，并同时为 MCP 子进程设置 `TEMP/TMP`。该目录不得存放业务数据，验证后按需清理。

### 6.3 `-a never` 不等于自动批准 MCP tools

在非交互模式中，未配置工具 approval 时，MCP 调用可能返回：

```text
user cancelled MCP tool call
```

验证过的做法是同时使用：

```toml
enabled_tools = ["本阶段需要的工具"]
default_tools_approval_mode = "approve"
```

`approve` 会自动批准白名单中的每个工具，因此工具列表必须按阶段保持最小化。

### 6.4 `--ignore-user-config` 后仍应显式关闭无关能力

为了减少产品内置 app/plugin 的初始化和网络重试，命令同时禁用 `apps/plugins/remote_plugin`。启动日志中的模型缓存或网络预热 warning 不等于 MCP 操作失败；应以 tool started/completed 和最终 JSON 为准。

### 6.5 不允许 MCP 失败后的非受控回退

Prompt 必须声明：若正式 `local-onenote` MCP 不可用，立即停止，不得通过临时 Python/PowerShell 直连 COM。否则 agent 可能尝试自行诊断，而诊断子进程未必继承预期权限环境。

## 7. 安全检查清单

运行前：

- [ ] 用户明确触发本次真实 COM 验证；
- [ ] 用户明确授权 Codex 服务接收隔离 Notebook 元数据；
- [ ] Notebook 名称、ID 和路径唯一；
- [ ] `health_check` 中策略与当前阶段匹配；
- [ ] `enabled_tools` 不包含本阶段不需要的 mutation；
- [ ] Delete、永久 Delete、raw XML 保持关闭，除非进入独立 Delete 场景；
- [ ] 输出目录不存在或明确禁止覆盖。

运行后：

- [ ] 保存最终 JSON 和关键对象 ID；
- [ ] 重新运行只读 snapshot；
- [ ] 人工检查 OneNote UI、附件/图片和同步状态；
- [ ] 保留 onepkg 或截图基线；
- [ ] 确认没有修改全局/项目 Codex 配置；
- [ ] 删除仅用于 schema 探测的临时 `CODEX_HOME`。

## 8. 版本边界

上述参数和 approval 行为来自本机 `codex --help`、`codex features list`、本地 CLI schema 字符串以及 `0.144.6` 实际运行结果。Codex CLI 升级后必须先用只读三工具白名单重新验证；不能把本页当成跨版本稳定 API 保证。
