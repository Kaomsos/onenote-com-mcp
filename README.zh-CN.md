# Local OneNote MCP

[English](README.md) | **简体中文**

面向 **Windows 上 Microsoft OneNote Desktop** 的 local-first [MCP](https://modelcontextprotocol.io/) 服务器。它只通过本地 PowerShell COM client 调用原生 OneNote COM API——不使用 Microsoft Graph、Azure、API key、在线 OAuth，没有遥测和远程内容处理，也不直接编辑 `.one` 文件。

你的笔记永远不会离开本机。

## 亮点

- **53 个类型化工具**：覆盖层级浏览、元数据查询、全文搜索、Page 内容读取、创建、重命名、排序、换父级、Page 内容修改、可恢复删除、复制、重建式移动、PDF 导出、UI 导航和 Notebook 生命周期。
- **默认 fail-closed。** 七个 mutation 授权门全部默认关闭；只读配置无法创建、修改或删除任何内容。
- **精确 ID 定位。** 写操作以精确 OneNote 对象 ID 加乐观 confirmation 字段为目标，绝不按名称模糊匹配。即使目标 Section 已有同标题一级 Page，Page Copy/Move 仍会创建新的 fresh Page。
- **只做非永久删除。** 公开删除工具只把对象移入 OneNote 回收站；永久删除工具不对外发布。
- **有界工作量。** 搜索、复制和批量 mutation 受显式预算约束；预算耗尽是显式失败，不会静默无界执行。
- **Content-free 审计。** 日志只记录操作名和耗时，绝不记录笔记内容、payload 或原始工具参数。

## 环境要求

- Windows 10 或 11
- Microsoft OneNote Desktop（不是旧版 Windows 10 UWP 应用）
- Python 3.11+
- Node.js 18+（仅 npm 全局 launcher 需要）
- 可选：OneMore Desktop Add-in，用于富 Markdown 编译

## 安装

推荐使用全局 launcher：

```powershell
npm install -g github:Peteroooooooo/local-onenote-mcp
```

参与仓库开发：

```powershell
git clone https://github.com/Peteroooooooo/local-onenote-mcp
cd local-onenote-mcp
uv sync --all-groups
uv run pytest
```

## 快速开始

把服务器加入你的 MCP 客户端。Claude Desktop 或 Cursor（`mcpServers` JSON）：

```json
{
  "mcpServers": {
    "local-onenote": {
      "command": "local-onenote-mcp",
      "env": {
        "LOCAL_ONENOTE_MCP_TIMEOUT": "90",
        "LOCAL_ONENOTE_ENABLE_WRITES": "false"
      }
    }
  }
}
```

七个授权门（`Create`、`Writes`、`Deletes`、`Organize`、`Local File IO`、`UI Control`、`Notebook Lifecycle`）全部默认 `false`；只开启你需要的，改完配置后重启 MCP 客户端。每个会话先调用 `health_check`，它绝不会启动 OneNote。可选的本地 debug trace（`LOCAL_ONENOTE_MCP_DEBUG_TRACE` + `LOCAL_ONENOTE_MCP_DEBUG_DIR`）默认关闭且不是遥测——见[配置文档](docs-public/zh-CN/user-guide/configuration.md#本地-debug-trace可选默认关闭)。

完整的安装步骤、TOML 客户端示例、全部环境变量和完整工具目录见使用文档：

- [快速上手](docs-public/zh-CN/user-guide/getting-started.md)
- [配置与授权门](docs-public/zh-CN/user-guide/configuration.md)
- [工具总览](docs-public/zh-CN/user-guide/tools.md)
- [安全模型与限制](docs-public/zh-CN/user-guide/safety-model.md)
- [常见问题与故障排查](docs-public/zh-CN/user-guide/faq.md)

## 本项目明确不做的事

- 不使用 Microsoft Graph、Azure 或在线 OAuth——服务器完全针对本地 OneNote Desktop 进程工作。
- 不上传、同步或远程处理笔记内容。
- 不直接读写二进制 `.one` 文件。
- 不做"绝对安全"或"支持所有 OneNote 版本"的宣传：已验证行为都注明证据范围，未支持的对象 fail closed。

## 文档

| 读者 | 入口 |
| --- | --- |
| 用户 | [使用文档](docs-public/zh-CN/user-guide/getting-started.md)（[English](docs-public/en/user-guide/getting-started.md)） |
| 贡献者 | [开发文档](docs-public/zh-CN/dev-guide/project-structure.md)（[English](docs-public/en/dev-guide/project-structure.md)） |
| 契约级细节 | [内部设计文档](docs/README.md)——架构与工具契约的权威来源 |

## 参与贡献

见[贡献指南](docs-public/zh-CN/dev-guide/contributing.md)。有一条规则高于一切：自动化智能体、pytest、CI、hook、timer 和后台任务绝不能运行真实 OneNote mutation scenario——真实后端验证永远由人显式启动。[手动验证框架](docs-public/zh-CN/dev-guide/manual-validation.md)解释了它的工作方式。

在专门的安全政策发布前，请通过 GitHub Issue 报告疑似安全问题，不要附带笔记内容或个人数据。

## 许可证

本项目以 GNU General Public License v3.0 or later（GPL-3.0-or-later）发布，见 [LICENSE](LICENSE)。

## Credits

本项目源自一个较早的 MIT 许可 OneNote COM 项目的 fork。上游署名细节（仓库、fork commit 与保留的许可声明）将在首次公开发布前完成核实，并记录在 NOTICE 文件中。
