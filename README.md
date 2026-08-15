# Local OneNote MCP

A local-first MCP server for Microsoft OneNote Desktop on Windows. It uses the local OneNote COM API through a fixed PowerShell bridge—no Microsoft Graph, Azure, API keys, online OAuth, telemetry, remote content processing, or direct `.one` file editing.

## Design and safety

- Typed Notebook, SectionGroup, Section, Page, and PageContentObject contracts; mutations use exact IDs and optimistic confirmation fields.
- A canonical 52-operation Registry owns exposure, category, authorization, execution strategy, handler, audit, and retry semantics.
- Reads share a process-local lease; mutation and lifecycle effects use exclusive coordination through preflight, execution, reconciliation, and stable read-back.
- Writes, Deletes, Organize, Copy, Local File IO, UI Control, and Notebook Lifecycle are seven independent, default-off authorization categories.
- Raw XML, generic hierarchy mutation, public planning tokens, and an advanced MCP profile are not exposed.
- OneNote Desktop readiness means both `ONENOTE.EXE` and a visible top-level window. `health_check` is always check-only.

Current architecture and contracts: [documentation map](docs/README.md), [tool contracts](docs/design/tool_contracts.md), [object model](docs/design/object_model.md), [Operation Runtime](docs/design/operation_runtime.md), and [tool-surface convergence record](docs/todo/034_pre_user_testing_tool_surface_convergence.md).

## Requirements

- Windows 10 or 11
- Microsoft OneNote Desktop, not the legacy Windows 10 UWP app
- Python 3.11+
- Node.js/npm for the standard global launcher
- OneMore Desktop Add-in only when rich Markdown compilation is wanted

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

The checked-in Codex and Claude Code configurations start the server with `uv run --locked local-onenote-mcp` after project trust/approval.

## Client configuration

Claude Desktop:

```json
{
  "mcpServers": {
    "local-onenote": {
      "command": "local-onenote-mcp",
      "env": {
        "LOCAL_ONENOTE_MCP_TIMEOUT": "90",
        "LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS": "60000",
        "LOCAL_ONENOTE_ENABLE_WRITES": "false",
        "LOCAL_ONENOTE_ENABLE_DELETES": "false",
        "LOCAL_ONENOTE_ENABLE_ORGANIZE": "false",
        "LOCAL_ONENOTE_ENABLE_COPY": "false",
        "LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO": "false",
        "LOCAL_ONENOTE_ENABLE_UI_CONTROL": "false",
        "LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE": "false"
      }
    }
  }
}
```

Codex/Cursor TOML:

```toml
[mcp_servers.local-onenote]
type = "stdio"
command = "local-onenote-mcp"
startup_timeout_ms = 120000

[mcp_servers.local-onenote.env]
LOCAL_ONENOTE_MCP_TIMEOUT = "90"
LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS = "60000"
LOCAL_ONENOTE_ENABLE_WRITES = "false"
LOCAL_ONENOTE_ENABLE_DELETES = "false"
LOCAL_ONENOTE_ENABLE_ORGANIZE = "false"
LOCAL_ONENOTE_ENABLE_COPY = "false"
LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO = "false"
LOCAL_ONENOTE_ENABLE_UI_CONTROL = "false"
LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE = "false"
```

Restart the MCP client after changing configuration.

## Current user tool surface: 52 tools

The only production profile is organized by user task:

| Category | Tools |
| --- | --- |
| Session | `health_check`, `launch_onenote_gui` |
| Hierarchy Browse | `list_notebooks`, `get_hierarchy_path`, `expand_notebook`, `expand_section_group`, `expand_section`, `expand_page`, `expand_hierarchy` |
| Metadata Get | `get_notebook_metadata`, `get_section_group_metadata`, `get_section_metadata`, `get_page_metadata` |
| Query & Search | `query_notebook`, `query_section_group`, `query_section`, `query_page`, `search_pages` |
| Page Content Read | `get_page_text`, `list_page_content_objects`, `get_page_object_binary` |
| Hyperlink | `get_hyperlink` |
| Create | `create_notebook`, `create_section_group`, `create_section`, `create_page` |
| Rename | `rename_page`, `rename_section_group`, `rename_section` |
| Reorder | `reorder_page`, `reorder_section` |
| Organize | `reparent_page`, `reparent_section`, `reparent_section_group` |
| Page Content Mutation | `append_page_content`, `add_page_image_from_file`, `replace_page_body`, `delete_page_content_object` |
| Recoverable Delete | `delete_page`, `delete_section`, `delete_section_group` |
| Copy | `copy_page`, `copy_section`, `copy_section_group`, `copy_notebook` |
| Reconstructive Move | `move_page`, `move_section`, `move_section_group` |
| Export | `export_object_to_pdf` |
| UI Navigation | `navigate_to` |
| Notebook Lifecycle | `request_notebook_sync`, `close_notebook` |

`resolve_identifier`, `get_page_xml`, `navigate_to_url`, `get_special_locations`, and `get_parent` are non-registered internal/incubating capabilities. There are no compatibility aliases for renamed tools and no environment switch can expose them.

Every public call returns one of these exact envelope shapes:

```json
{"ok":true,"result":{},"warnings":[],"execution":{}}
```

```json
{"ok":false,"error":{"code":"...","message":"...","details":{}},"execution":{}}
```

The full parameter, scope, response, effect, budget, and authorization contract is in [tool contracts](docs/design/tool_contracts.md).

## Authorization

All seven gates default to false:

| Gate | Environment variable | Important combinations |
| --- | --- | --- |
| Writes | `LOCAL_ONENOTE_ENABLE_WRITES` | Create, Rename, Reorder, Append |
| Deletes | `LOCAL_ONENOTE_ENABLE_DELETES` | Recoverable Delete; Replace also needs Writes |
| Organize | `LOCAL_ONENOTE_ENABLE_ORGANIZE` | Reparent also needs Writes |
| Copy | `LOCAL_ONENOTE_ENABLE_COPY` | Copy also needs Writes; Move also needs Deletes |
| Local File IO | `LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO` | Export; file image addition also needs Writes |
| UI Control | `LOCAL_ONENOTE_ENABLE_UI_CONTROL` | `launch_onenote_gui`, `navigate_to` |
| Notebook Lifecycle | `LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE` | sync request and close |

The normal delete tools are always non-permanent and have no `permanently` parameter. Permanent-delete tools are not published.

## Important behavior

- `health_check` never launches OneNote. `launch_onenote_gui()` is a separate UI Control operation with no parameters, at most one trusted process-launch request, and bounded GUI-readiness observation. Its real start/rejection/idempotency check uses the standalone, human-gated [`launch_onenote_gui_check.py`](tests/manual_validation/launch_onenote_gui_check.py), which is outside the Scenario registry and `all`.
- Query reads hierarchy metadata; `search_pages` uses OneNote's live index for Page body discovery. Both return candidates; mutations still require an exact ID.
- Typed Expand parameters name their object type (`notebook_id`, `section_group_id`, `section_id`, `page_id`).
- Page range is `page_scope="page_only" | "indentation_subtree"`; optional values use `null`, not empty-string sentinels.
- Copy/Move are single calls. Planning stays inside the operation; clients never carry `plan_digest` or replay state.
- Move is reconstructive: verified Copy followed by a non-permanent source delete. It creates new IDs and can return partial or indeterminate results that must not be blindly retried.
- `export_object_to_pdf` only writes a new PDF and never overwrites an existing path.
- `request_notebook_sync` proves request acceptance, not synchronization completion.
- SectionGroup reorder is unsupported because the observed backend exposes a fixed name order rather than a stable mutable sibling order.

## OneMore and Markdown

OneMore is optional. When its Markdig assembly is installed, Markdown page creation and append can compile common Markdown into OneNote HTML. A custom assembly location can be configured with:

```toml
[mcp_servers.local-onenote.env]
LOCAL_ONENOTE_MARKDIG_DLL = "C:\\path\\to\\Markdig.Signed.dll"
```

Without OneMore, plain text and validated HTML paths remain available.

## Verification

Pure automated tests:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Read-only transport smoke test; optionally pass one exact open Notebook COM ID:

```powershell
uv run python scripts\smoke_mcp.py --tools-only
uv run python scripts\smoke_mcp.py
uv run python scripts\smoke_mcp.py --notebook "{EXACT-NOTEBOOK-ID}"
```

`--tools-only` validates the exact 52-item MCP `tools/list` order, descriptions and schemas without probing or connecting to OneNote. Add `--include-tool-snapshot` when the complete transport projection is needed in the JSON output. The other forms require an already-running visible OneNote Desktop GUI and remain read-only.

Manual-validation plans are safe to inspect with `--dry-run`. Only the user may remove `--dry-run` and run a real OneNote scenario:

```powershell
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --verbosity normal
.venv\Scripts\python.exe tests\manual_validation\run.py all --dry-run --verbosity normal
```

Real validation uses new disposable data, exact IDs, minimal static permissions, before/after evidence, and preserved failure artifacts. See [isolated mutation validation](docs/dev/isolated_mutation_validation.md).

## Limits

- Windows desktop and single-user local sessions only; no cloud or cross-process transaction boundary.
- Reparent stays within one Notebook. Cross-Notebook Section/SectionGroup transfer uses reconstructive Move.
- Page-body replacement and recursive Copy/Move are multi-step and non-atomic.
- External inbound links cannot retain identity across reconstructive Copy/Move.
- Rich-object Copy fidelity is allowlisted and evidence-bound. Unsupported or unverified objects fail closed; see [copy content exclusions](docs/lesson/copy_content_type_exclusions.md).
- OneNote may normalize standalone display-equation whitespace during COM writes; see the [observed limitation](docs/lesson/display_equation_com_leading_whitespace_normalization.md).
