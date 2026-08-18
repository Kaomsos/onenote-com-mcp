# 快速上手

[English](../../en/user-guide/getting-started.md) | [文档首页](../../README.zh-CN.md)

Local OneNote MCP 是面向 Windows 上 Microsoft OneNote Desktop 的 local-first MCP 服务器。它通过固定 PowerShell bridge 使用原生 OneNote COM API。任何内容都不会被上传：没有 Microsoft Graph、Azure、在线 OAuth，也没有遥测。

## 前置条件

- **Windows 10 或 11。** 服务器依赖 OneNote COM API，因此仅支持 Windows。
- **Microsoft OneNote Desktop。** 完整桌面应用——不是旧版 Windows 10 UWP 应用。
- **Python 3.11+。**
- **Node.js 18+**（如果通过 npm 全局 launcher 安装）。
- **OneMore Desktop Add-in**（可选）：在创建或追加 Page 内容时把富 Markdown 编译为 OneNote HTML。没有它时，纯文本与已验证 HTML 路径仍然可用。

## 安装

推荐使用全局 launcher：

```powershell
npm install -g github:Peteroooooooo/local-onenote-mcp
```

它会安装一个 MCP 客户端可以直接启动的 `local-onenote-mcp` 命令。

参与开发时，克隆仓库并使用 [uv](https://docs.astral.sh/uv/)：

```powershell
git clone https://github.com/Peteroooooooo/local-onenote-mcp
cd local-onenote-mcp
uv sync --all-groups
uv run pytest
```

## 连接 MCP 客户端

把服务器加入你的 MCP 客户端配置。完整的 JSON/TOML 示例和全部环境变量见[配置与授权门](configuration.md)。Claude Desktop 或 Cursor 的最小只读配置：

```json
{
  "mcpServers": {
    "local-onenote": {
      "command": "local-onenote-mcp"
    }
  }
}
```

不设置任何环境变量时，服务器完全只读：七个 mutation 授权门全部默认关闭。

改完配置后重启 MCP 客户端。

## 首次会话

1. 启动 OneNote Desktop 并保持窗口可见。服务器绝不会隐式启动 OneNote。
2. 在 MCP 客户端中调用 `health_check`。它始终只做检查：报告 OneNote 是否就绪（存在运行中的 `ONENOTE.EXE` **且**有可见顶层窗口）、当前 policy 和已配置的预算——绝不会启动任何程序。
3. 如果 health 报告未就绪且你启用了 `UI Control` 门，可调用 `launch_onenote_gui()`，然后再次 `health_check`；否则手动启动 OneNote Desktop。
4. 用只读工具浏览：`list_notebooks`，然后 `expand_notebook` / `expand_section` 等，`search_pages`、`get_page_text`。

## 验证传输层（可选）

在仓库 checkout 中可运行只读 smoke 测试：

```powershell
uv run python scripts\smoke_mcp.py --tools-only
```

`--tools-only` 校验精确的 53 项工具列表，不连接 OneNote。去掉该参数则对已运行且可见的 OneNote Desktop 执行只读探测。

## 下一步

- [配置与授权门](configuration.md)——精确启用你需要的能力。
- [工具总览](tools.md)——53 个工具分别做什么。
- [安全模型与限制](safety-model.md)——服务器如何保护你的笔记本。
- [常见问题与故障排查](faq.md)。
