# 配置与授权门

[English](../../en/user-guide/configuration.md) | [文档首页](../../README.zh-CN.md)

服务器完全通过 MCP 客户端传入的环境变量配置。所有危险能力默认**关闭**。

## 客户端配置示例

Claude Desktop 或 Cursor（`mcpServers` JSON）：

```json
{
  "mcpServers": {
    "local-onenote": {
      "command": "local-onenote-mcp",
      "env": {
        "LOCAL_ONENOTE_MCP_TIMEOUT": "90",
        "LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS": "60000",
        "LOCAL_ONENOTE_ENABLE_CREATE": "false",
        "LOCAL_ONENOTE_ENABLE_WRITES": "false",
        "LOCAL_ONENOTE_ENABLE_DELETES": "false",
        "LOCAL_ONENOTE_ENABLE_ORGANIZE": "false",
        "LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO": "false",
        "LOCAL_ONENOTE_ENABLE_UI_CONTROL": "false",
        "LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE": "false"
      }
    }
  }
}
```

Codex 或 Grok Build（TOML）：

```toml
[mcp_servers.local-onenote]
command = "local-onenote-mcp"
startup_timeout_sec = 120
tool_timeout_sec = 120

[mcp_servers.local-onenote.env]
LOCAL_ONENOTE_MCP_TIMEOUT = "90"
LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS = "60000"
LOCAL_ONENOTE_ENABLE_CREATE = "false"
LOCAL_ONENOTE_ENABLE_WRITES = "false"
LOCAL_ONENOTE_ENABLE_DELETES = "false"
LOCAL_ONENOTE_ENABLE_ORGANIZE = "false"
LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO = "false"
LOCAL_ONENOTE_ENABLE_UI_CONTROL = "false"
LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE = "false"
```

改完配置后重启 MCP 客户端——服务器在启动时读取一次 policy，运行期间绝不扩权。

## 七个授权门

每个门是一个独立的环境变量，默认 `false`，控制一类独立风险。部分工具需要多个门的组合。

| 门 | 环境变量 | 控制 | 重要组合 |
| --- | --- | --- | --- |
| Create | `LOCAL_ONENOTE_ENABLE_CREATE` | Notebook/SectionGroup/Section 创建 | Page 创建还需要 Writes |
| Writes | `LOCAL_ONENOTE_ENABLE_WRITES` | 重命名、排序、Page 内容追加/替换 | Copy 还需要 Create |
| Deletes | `LOCAL_ONENOTE_ENABLE_DELETES` | 可恢复（非永久）删除 | Page 正文替换还需要 Writes；Move 需要 Create + Writes + Deletes |
| Organize | `LOCAL_ONENOTE_ENABLE_ORGANIZE` | Notebook 内换父级 | Reparent 还需要 Writes |
| Local File IO | `LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO` | PDF 导出、从本地文件添加图片 | 图片添加还需要 Writes |
| UI Control | `LOCAL_ONENOTE_ENABLE_UI_CONTROL` | `launch_onenote_gui`、`navigate_to` | — |
| Notebook Lifecycle | `LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE` | `request_notebook_sync`、`close_notebook` | — |

说明：

- 公开删除工具始终非永久（进回收站），没有 `permanently` 参数。永久删除工具完全不发布。
- Copy 需要 Create + Writes。重建式 Move（先验证 Copy，再非永久删除源）额外需要 Deletes。

## 运行时设置

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `LOCAL_ONENOTE_MCP_TIMEOUT` | — | 单次 bridge 操作超时秒数（如 `90`） |
| `LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS` | — | 返回文本大小上限（如 `60000`） |
| `LOCAL_ONENOTE_MCP_DEBUG_TRACE` | `false` | 启用仅本地的 Runtime debug trace JSONL（严格布尔值） |
| `LOCAL_ONENOTE_MCP_DEBUG_DIR` | `~/.onenote-mcp/debug-trace` | 可选的 session JSONL 输出绝对目录。未设置时，服务器使用并创建该用户本地默认目录。不授权任何 mutation。 |
| `LOCAL_ONENOTE_MARKDIG_DLL` | 自动探测 | OneMore `Markdig.Signed.dll` 的显式路径，用于 Markdown 编译 |

## Batch Mutation 预算

Batch Mutation 有独立的 content-free 预算，由 `health_check.batch_mutation_budget` 投影。默认值：catalog resources `100000`、effective resources `1000`、effective Pages `200`、direct siblings `1000`、Page 请求内容 `500000` 字符。可用以下变量覆盖：

- `LOCAL_ONENOTE_MAX_BATCH_CATALOG_RESOURCES`
- `LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_RESOURCES`
- `LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_PAGES`
- `LOCAL_ONENOTE_MAX_BATCH_DIRECT_SIBLINGS`
- `LOCAL_ONENOTE_MAX_BATCH_PAGE_CONTENT_CHARS`

catalog 中发现的无关对象不消耗 effective target 预算。

## 本地 debug trace（可选，默认关闭）

这**不是遥测**。启用后，MCP 进程会向你指定的目录写入 content-free JSONL，用 `tool_call.*` 记录一次 tool 调用的生命周期，用单独的 backend 行记录具体内部 `operation`，终态再给出 backend 次数摘要和稳定 error code——**不**记录原始参数、Page 正文、对象 ID、secret 或完整路径。

```json
{
  "env": {
    "LOCAL_ONENOTE_MCP_DEBUG_TRACE": "true",
    "LOCAL_ONENOTE_MCP_DEBUG_DIR": "C:\\\\Users\\\\you\\\\local-onenote-debug"
  }
}
```

规则：

- 仅设置 `LOCAL_ONENOTE_MCP_DEBUG_TRACE=true` 即可：`LOCAL_ONENOTE_MCP_DEBUG_DIR` 未设置时，服务器会创建并使用上表的用户本地默认目录。显式目录必须为绝对路径，缺失的父目录会创建；空值、相对路径、reparse point、非目录或无法创建的路径 fail closed。
- trace 关闭时零目录创建、零文件写入，即使配置了 `LOCAL_ONENOTE_MCP_DEBUG_DIR` 也不隐式启用。
- 修改 trace 配置后需重启 MCP 客户端。
- session 文件有容量上限；分享前请审查——其中包含 tool 名称和时序元数据。
- `health_check.debug_trace` 投影 `enabled` 以及输出目录是否已配置/可写（不含完整路径）。

本地 smoke（无需真实 OneNote mutation）：

```powershell
$env:LOCAL_ONENOTE_MCP_DEBUG_TRACE = "true"
$env:LOCAL_ONENOTE_MCP_DEBUG_DIR = "C:\Users\you\local-onenote-debug"
# 重启 MCP 后调用 health_check、一个只读 tool 和一个被 policy 拒绝的 mutation tool
```

## 权限组合建议

- **只读探索**：全部保持关闭。浏览、查询、搜索、Page 读取和超链接解析都可用。
- **记笔记助手**：`Create` + `Writes`，可以创建 Section/Page 并追加内容。
- **整理笔记**：追加 `Organize`（换父级）和/或 `Deletes`（可恢复删除，Move 也需要）。
- **按场景取最小闭包，绝不多开。** 服务器在 policy 层就拒绝未授权调用，不会产生任何后端工作。

每个工具的参数级完整契约见维护者文档：[tool contracts](../../../docs/design/tool_contracts.md)。
