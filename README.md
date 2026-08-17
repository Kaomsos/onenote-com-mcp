# Local OneNote MCP

A local-first MCP server for Microsoft OneNote Desktop on Windows. It uses the local OneNote COM API through a fixed PowerShell bridge—no Microsoft Graph, Azure, API keys, online OAuth, telemetry, remote content processing, or direct `.one` file editing.

## Design and safety

- Typed Notebook, SectionGroup, Section, Page, and PageContentObject contracts; mutations use exact IDs and optimistic confirmation fields.
- A canonical 53-operation Registry owns exposure, category, authorization, independent platform-preflight policy, execution strategy, handler, audit, and retry semantics.
- Reads share a process-local lease; mutation and lifecycle effects use exclusive coordination through preflight, execution, reconciliation, and stable read-back.
- Create, Writes, Deletes, Organize, Local File IO, UI Control, and Notebook Lifecycle are seven independent, default-off authorization categories.
- Raw XML, generic hierarchy mutation, public planning tokens, and an advanced MCP profile are not exposed.
- OneNote Desktop readiness means both `ONENOTE.EXE` and a visible top-level window. Every authorized effect checks this prerequisite after authorization and before coordination or backend work; pure reads do not. `health_check` is always check-only and `launch_onenote_gui` is the explicit recovery effect.

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

The repository includes project-scoped MCP configuration for Claude Code (`.mcp.json`), Codex (`.codex/config.toml`), Cursor (`.cursor/mcp.json`), and Grok Build (`.grok/config.toml`). Each uses `uv run --locked local-onenote-mcp`. The reusable Claude Code, Codex, and Cursor profiles keep all seven effect gates off; the developer-owned Grok user-testing profile explicitly enables only Writes and UI Control for disposable testing. Codex remains disabled until the user trusts and enables it; Claude Code and Cursor apply their own project-server approval flow.

## Client configuration

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

Codex or Grok Build TOML:

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

Restart the MCP client after changing configuration.

## Current user tool surface: 53 tools

The only production profile is organized by user task:

| Category | Tools |
| --- | --- |
| Session | `health_check`, `launch_onenote_gui` |
| Hierarchy Browse | `list_notebooks`, `get_hierarchy_path`, `expand_notebook`, `expand_section_group`, `expand_section`, `expand_page`, `expand_hierarchy` |
| Metadata Get | `get_notebook_metadata`, `get_section_group_metadata`, `get_section_metadata`, `get_page_metadata` |
| Query & Search | `query_notebook`, `query_section_group`, `query_section`, `query_page`, `search_pages` |
| Page Content Read | `get_page_text`, `get_page_content_objects`, `get_page_content_object_binary` |
| Hyperlink | `get_hyperlink` |
| Create | `create_notebook`, `create_section_group`, `create_section`, `create_page` |
| Rename | `rename_page`, `rename_section_group`, `rename_section` |
| Reorder | `reorder_page`, `reorder_section`, `sort_children` |
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
| Create | `LOCAL_ONENOTE_ENABLE_CREATE` | Notebook/SectionGroup/Section creation; Page creation also needs Writes |
| Writes | `LOCAL_ONENOTE_ENABLE_WRITES` | Rename, Reorder/Sort, Append; Copy also needs Create |
| Deletes | `LOCAL_ONENOTE_ENABLE_DELETES` | Recoverable Delete; Replace also needs Writes |
| Organize | `LOCAL_ONENOTE_ENABLE_ORGANIZE` | Reparent also needs Writes |
| Local File IO | `LOCAL_ONENOTE_ENABLE_LOCAL_FILE_IO` | Export; file image addition also needs Writes |
| UI Control | `LOCAL_ONENOTE_ENABLE_UI_CONTROL` | `launch_onenote_gui`, `navigate_to` |
| Notebook Lifecycle | `LOCAL_ONENOTE_ENABLE_NOTEBOOK_LIFECYCLE` | sync request and close |

The normal delete tools are always non-permanent and have no `permanently` parameter. Permanent-delete tools are not published.

## Important behavior

- Call `health_check` at the start of an MCP session. It never launches OneNote. Before every authorized effect, the Runtime independently requires an existing visible OneNote GUI; authorization rejection happens first, and a readiness rejection produces zero backend calls. Pure reads remain usable without this effect prerequisite.
- If health reports not ready, call `launch_onenote_gui()` with UI Control enabled, call `health_check` again, then retry the original authorized effect. If UI Control is disabled, set `LOCAL_ONENOTE_ENABLE_UI_CONTROL=true` and restart the MCP server, or start OneNote Desktop manually. Launch is a separate no-parameter UI effect with at most one trusted process-launch request and bounded readiness observation; its real acceptance check uses the standalone, human-gated [`launch_onenote_gui_check.py`](tests/manual_validation/launch_onenote_gui_check.py), outside the Scenario registry and `all`.
- Query reads hierarchy metadata; `search_pages` uses OneNote's live index for Page body discovery. Both return candidates; mutations still require an exact ID.
- Typed Expand parameters name their object type (`notebook_id`, `section_group_id`, `section_id`, `page_id`).
- `get_page_text` defaults to bounded `sanitized_html_v1`, which preserves reviewed formatting, links, lists, tags, tables, and canonical Presentation MathML without exposing raw Page XML or binary payloads. Set `mode="plain"` when the legacy `{text, chars}` projection is preferred.
- Page range is `page_scope="page_only" | "indentation_subtree"`; optional values use `null`, not empty-string sentinels.
- Existing `create_*`, `rename_*`, `reparent_*`, and recoverable `delete_*` tools support either their original single-item fields or a bounded `items` batch (1–20); there are no separate `batch_*` tools. A batch is preflighted as a whole, executes in input order, stops on the first failed or uncertain item, and never performs a broad rollback or mutation replay. Partial responses preserve `applied/failed/not_attempted` item states and require live-state inspection before recovery. Successful Reparent batches additionally return a final live, content-free hierarchy summary for every input item.
- `sort_children` stably sorts only a confirmed parent's complete direct-child sequence by `name`, `created`, or `modified`, ascending or descending. Child type is inferred: Notebook/SectionGroup sorts Sections; Section/Page sorts Pages. Leveled Pages move as complete indentation blocks; SectionGroups are never sorted and recursive mode is unsupported.
- Copy/Move are single calls. Copy requires Create + Writes; Move additionally requires Deletes. Planning stays inside the operation; clients never carry `plan_digest` or replay state.
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

`--tools-only` validates the exact 53-item MCP `tools/list` order, descriptions and schemas without probing or connecting to OneNote. Add `--include-tool-snapshot` when the complete transport projection is needed in the JSON output. The other forms require an already-running visible OneNote Desktop GUI and remain read-only.

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
