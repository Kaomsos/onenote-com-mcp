# Getting Started

[简体中文](../../zh-CN/user-guide/getting-started.md) | [Documentation home](../../README.md)

Local OneNote MCP is a local-first MCP server for Microsoft OneNote Desktop on Windows. It uses the native OneNote COM API through a fixed PowerShell bridge. Nothing is uploaded anywhere: no Microsoft Graph, no Azure, no online OAuth, no telemetry.

## Prerequisites

- **Windows 10 or 11.** The server is Windows-only because it depends on the OneNote COM API.
- **Microsoft OneNote Desktop.** The full desktop application — not the legacy Windows 10 UWP app.
- **Python 3.11+.**
- **Node.js 18+** if you install through the npm global launcher.
- **OneMore Desktop Add-in** (optional) if you want rich Markdown compiled into OneNote HTML when creating or appending page content. Without it, plain text and validated HTML paths remain available.

## Install

Recommended global launcher:

```powershell
npm install -g github:Peteroooooooo/local-onenote-mcp
```

This installs a `local-onenote-mcp` command that MCP clients can spawn directly.

Alternatively, for development, clone the repository and use [uv](https://docs.astral.sh/uv/):

```powershell
git clone https://github.com/Peteroooooooo/local-onenote-mcp
cd local-onenote-mcp
uv sync --all-groups
uv run pytest
```

## Connect an MCP client

Add the server to your MCP client configuration. See [Configuration](configuration.md) for complete JSON and TOML examples and every environment variable. A minimal read-only setup for Claude Desktop or Cursor:

```json
{
  "mcpServers": {
    "local-onenote": {
      "command": "local-onenote-mcp"
    }
  }
}
```

With no environment variables set, the server runs fully read-only: all seven mutation authorization gates default to off.

Restart the MCP client after changing configuration.

## First session

1. Start OneNote Desktop and keep it visible. The server never launches OneNote implicitly.
2. Call `health_check` from your MCP client. It is always check-only: it reports whether OneNote is ready (a running `ONENOTE.EXE` **and** a visible top-level window), the active policy, and configured budgets — and never launches anything.
3. If health reports not ready and you have enabled the `UI Control` gate, call `launch_onenote_gui()`, then `health_check` again. Otherwise start OneNote Desktop manually.
4. Browse with read tools: `list_notebooks`, then `expand_notebook` / `expand_section` and friends, `search_pages`, `get_page_text`.

## Verify the transport (optional)

From a repository checkout, a read-only smoke test is available:

```powershell
uv run python scripts\smoke_mcp.py --tools-only
```

`--tools-only` validates the exact 53-item tool list without connecting to OneNote. Dropping the flag performs read-only probes against an already-running visible OneNote Desktop.

## Next steps

- [Configuration and authorization gates](configuration.md) — enable exactly the capabilities you need.
- [Tool overview](tools.md) — what each of the 53 tools does.
- [Safety model and limits](safety-model.md) — how the server protects your notebooks.
- [FAQ and troubleshooting](faq.md).
