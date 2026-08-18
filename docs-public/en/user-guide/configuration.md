# Configuration and Authorization Gates

[简体中文](../../zh-CN/user-guide/configuration.md) | [Documentation home](../../README.md)

The server is configured entirely through environment variables passed by your MCP client. Everything dangerous defaults to **off**.

## Client configuration examples

Claude Desktop or Cursor (`mcpServers` JSON):

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

Codex or Grok Build (TOML):

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

Restart the MCP client after changing configuration — the server reads its policy once at startup and never expands permissions while running.

## The seven authorization gates

Each gate is an independent environment variable, defaults to `false`, and controls a distinct risk category. Some tools require combinations of gates.

| Gate | Environment variable | Controls | Important combinations |
| --- | --- | --- | --- |
| Create | `LOCAL_ONENOTE_ENABLE_CREATE` | Notebook/SectionGroup/Section creation | Page creation also needs Writes |
| Writes | `LOCAL_ONENOTE_ENABLE_WRITES` | Rename, reorder/sort, page content append/replace | Copy also needs Create |
| Deletes | `LOCAL_ONENOTE_ENABLE_DELETES` | Recoverable (non-permanent) delete | Page body replace also needs Writes; Move needs Create + Writes + Deletes |
| Organize | `LOCAL_ONENOTE_ENABLE_ORGANIZE` | Reparent within a notebook | Reparent also needs Writes |
| Local File IO | `LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO` | PDF export, adding images from local files | Image addition also needs Writes |
| UI Control | `LOCAL_ONENOTE_ENABLE_UI_CONTROL` | `launch_onenote_gui`, `navigate_to` | — |
| Notebook Lifecycle | `LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE` | `request_notebook_sync`, `close_notebook` | — |

Notes:

- The public delete tools are always non-permanent (recycle bin) and have no `permanently` parameter. Permanent-delete tools are not published at all.
- Copy requires Create + Writes. Reconstructive Move (verified copy, then non-permanent source delete) additionally requires Deletes.

## Runtime settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `LOCAL_ONENOTE_MCP_TIMEOUT` | — | Per-operation bridge timeout in seconds (e.g. `90`) |
| `LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS` | — | Bound on returned text size (e.g. `60000`) |
| `LOCAL_ONENOTE_MARKDIG_DLL` | auto-detect | Explicit path to OneMore's `Markdig.Signed.dll` for Markdown compilation |

## Batch mutation budgets

Batch mutation has an independent, content-free budget, projected by `health_check.batch_mutation_budget`. Defaults: catalog resources `100000`, effective resources `1000`, effective pages `200`, direct siblings `1000`, page request content `500000` characters. Override with:

- `LOCAL_ONENOTE_MAX_BATCH_CATALOG_RESOURCES`
- `LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_RESOURCES`
- `LOCAL_ONENOTE_MAX_BATCH_EFFECTIVE_PAGES`
- `LOCAL_ONENOTE_MAX_BATCH_DIRECT_SIBLINGS`
- `LOCAL_ONENOTE_MAX_BATCH_PAGE_CONTENT_CHARS`

Unrelated objects found in the catalog do not consume the effective target budget.

## Choosing a permission profile

- **Read-only exploration:** leave everything off. Browsing, queries, search, page reads, and hyperlink resolution all work.
- **Note-taking assistant:** `Create` + `Writes` allows creating sections/pages and appending content.
- **Organizing:** add `Organize` (reparent) and/or `Deletes` (recoverable delete, and required for Move).
- **Everything a scenario needs, nothing more.** Prefer the minimal closure of gates for your workflow; the server rejects unauthorized calls at the policy layer before any backend work.

The full parameter-level contract for every tool is documented in the maintainer docs: [tool contracts](../../../docs/design/tool_contracts.md).
