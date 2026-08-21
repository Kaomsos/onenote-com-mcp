# Local OneNote MCP

**English** | [简体中文](README.zh-CN.md)

A local-first [MCP](https://modelcontextprotocol.io/) server for **Microsoft OneNote Desktop on Windows**. It talks to OneNote exclusively through the native COM API via a local PowerShell COM client — no Microsoft Graph, no Azure, no API keys, no online OAuth, no telemetry, no remote content processing, and no direct editing of `.one` files.

Your notes never leave your machine.

## Highlights

- **53 typed tools** covering hierarchy browsing, metadata queries, full-text search, page content reading, creation, rename, reorder, reparent, page content mutation, recoverable delete, copy, reconstructive move, PDF export, UI navigation, and notebook lifecycle.
- **Fail-closed by default.** All seven mutation authorization gates ship disabled; a read-only configuration cannot create, modify, or delete anything.
- **Exact-ID mutations.** Write operations target exact OneNote object IDs with optimistic confirmation fields — never fuzzy name matching. Page copy/move still create a fresh page when the destination section already has a same-title first-level page.
- **Non-permanent deletes only.** Public delete tools move objects to the OneNote recycle bin; permanent-delete tools are not published.
- **Bounded work.** Search, copy, and batch mutations run against explicit budgets; exhaustion is an explicit failure, not silent unbounded work.
- **Content-free audit.** Logs record operation names and timing, never notebook content, payloads, or raw tool arguments.

## Requirements

- Windows 10 or 11
- Microsoft OneNote Desktop (not the legacy Windows 10 UWP app)
- Python 3.11+
- Node.js 18+ (only for the npm global launcher)
- Optional: OneMore Desktop Add-in, for rich Markdown compilation

## Installation

Recommended global launcher:

```powershell
npm install -g github:Peteroooooooo/local-onenote-mcp
```

For repository development:

```powershell
git clone https://github.com/Peteroooooooo/local-onenote-mcp
cd local-onenote-mcp
uv sync --all-groups
uv run pytest
```

## Quick start

Add the server to your MCP client. Claude Desktop or Cursor (`mcpServers` JSON):

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

All seven authorization gates (`Create`, `Writes`, `Deletes`, `Organize`, `Local File IO`, `UI Control`, `Notebook Lifecycle`) default to `false`; enable only what you need and restart the MCP client after changing configuration. Start each session with `health_check`, which never launches OneNote. Optional local debug trace (`LOCAL_ONENOTE_MCP_DEBUG_TRACE` + `LOCAL_ONENOTE_MCP_DEBUG_DIR`) is off by default and is not telemetry — see [configuration](docs-public/en/user-guide/configuration.md#local-debug-trace-optional-off-by-default).

Full setup, TOML client examples, every environment variable, and the complete tool catalog live in the user guide:

- [Getting started](docs-public/en/user-guide/getting-started.md)
- [Configuration and authorization gates](docs-public/en/user-guide/configuration.md)
- [Tool overview](docs-public/en/user-guide/tools.md)
- [Safety model and limits](docs-public/en/user-guide/safety-model.md)
- [FAQ and troubleshooting](docs-public/en/user-guide/faq.md)

## What this project deliberately does not do

- No Microsoft Graph, Azure, or online OAuth — the server works entirely against the local OneNote Desktop process.
- No uploading, syncing, or remote processing of notebook content.
- No direct reading or writing of binary `.one` files.
- No "absolutely safe" or "works on every OneNote version" claims: verified behavior is documented with its evidence scope, and unsupported objects fail closed.

## Documentation

| Audience | Entry point |
| --- | --- |
| Users | [User guide](docs-public/en/user-guide/getting-started.md) ([中文](docs-public/zh-CN/user-guide/getting-started.md)) |
| Contributors | [Developer guide](docs-public/en/dev-guide/project-structure.md) ([中文](docs-public/zh-CN/dev-guide/project-structure.md)) |
| Contract-level detail | [Internal design docs](docs/README.md) — authoritative architecture and tool contracts |

## Contributing

See the [contributing guide](docs-public/en/dev-guide/contributing.md). One rule stands above all others: automated agents, pytest, CI, hooks, timers, and background tasks must never run real OneNote mutation scenarios — real-backend validation is always started explicitly by a human. The [manual validation framework](docs-public/en/dev-guide/manual-validation.md) explains how that works.

Until a dedicated security policy is published, please report suspected vulnerabilities through GitHub issues without including notebook content or personal data.

## License

This project is licensed under the GNU General Public License v3.0 or later (GPL-3.0-or-later). See [LICENSE](LICENSE).

## Credits

This project originated as a fork of an earlier MIT-licensed OneNote COM project. Upstream attribution details (repository, fork commit, and preserved license notices) are being finalized before the first public release and will be recorded in a NOTICE file.
