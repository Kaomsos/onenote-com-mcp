# Local OneNote MCP

A local Microsoft OneNote MCP server for Windows. It controls the OneNote desktop app through the local OneNote COM API—**requiring no Azure, Microsoft Graph, API keys, or online OAuth.**

---

## Design & Architecture

- **Local-Only Boundary:** Every operation executes directly through the local OneNote desktop installation. No data ever leaves your computer.
- **COM-First Engineering:** No direct binary `.one` file manipulation. All writes and reads leverage OneNote’s native COM engine, ensuring maximum data integrity and sync compatibility.
- **Safe Execution Bridge:** Inputs are passed safely through JSON-based temp files, completely avoiding PowerShell string interpolation or risk of command injections.
- **Typed Object Surface:** Stable Notebook, SectionGroup, Section, Page, and PageContentObject contracts with ID-only mutations.
- **Safe-by-Default Mutations:** Writes, deletes, permanent deletes, experimental moves, and raw development tools are independently disabled by default.
- **Bounded Search:** Local text scanning requires an explicit scope and enforces candidate, per-page, total-character, and time budgets.

> **Design Note:** PowerShell is leveraged as a reliable COM bridge because certain Windows/Office environments expose the OneNote COM interfaces directly to PowerShell while leaving them unavailable or restricted to Python's traditional automation libraries.

---

## Requirements

- **Windows 10 / 11**
- **Microsoft OneNote Desktop App** (Traditional version; not the legacy Windows 10 UWP app)
- **Python 3.11+**
- **Node.js & npm** (Required for the standard global launcher)
- **OneMore Desktop Add-in** *(Optional — only required to enable rich Markdown compilation)*

Verify your system environment:
```powershell
node -v
npm -v
python --version
```

---

## Quick Start (Recommended)

### 1. Install the global launcher
Open PowerShell and run:
```powershell
npm install -g github:Peteroooooooo/local-onenote-mcp
```
*(Once published on the npm registry, you will be able to install it using: `npm install -g local-onenote-mcp`)*

> ? **Performance & Startup Tip:** Running this server via a global installation (`npm install -g`) or direct local link is highly recommended over running it dynamically via ephemeral `npx` commands. This ensures the server operates 100% offline, resolves real physical paths consistently, caches the underlying Python virtual environment perfectly, and boots in under 1 second by avoiding slow GitHub network requests and redundant package reinstalls on startup.

### 2. Configure your MCP Client

#### Claude Desktop
Add this to your `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "local-onenote": {
      "command": "local-onenote-mcp",
      "env": {
        "LOCAL_ONENOTE_MCP_TIMEOUT": "90",
        "LOCAL_ONENOTE_MCP_MAX_TEXT_CHARS": "60000",
        "LOCAL_ONENOTE_ENABLE_WRITES": "false",
        "LOCAL_ONENOTE_ENABLE_DELETES": "false"
      }
    }
  }
}
```

#### Codex / Cursor (TOML)
Add this to your configuration:
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
```

*Restart your MCP client. Upon first execution, the launcher automatically creates a local Python virtual environment, installs the required packages, and hosts the stdio channel.*

---

## Alternative Installation Options

### Option A: Modern Python Toolchains (No npm required)

If you prefer pure-Python execution, you can configure your MCP client to invoke the server via standard Python package runners.

#### Using `uvx` (Ultra-fast, ephemeral execution)
```toml
[mcp_servers.local-onenote]
type = "stdio"
command = "uvx"
args = [
  "--from",
  "git+https://github.com/Peteroooooooo/local-onenote-mcp",
  "local-onenote-mcp"
]
startup_timeout_ms = 120000
```

#### Using `pipx` (Isolated user-space CLI)
```powershell
pipx install git+https://github.com/Peteroooooooo/local-onenote-mcp
```
Then configure:
```toml
[mcp_servers.local-onenote]
type = "stdio"
command = "local-onenote-mcp"
startup_timeout_ms = 120000
```

---

### Option B: Local Cloning & Active Development

To contribute or run the server from source:

1. **Clone the repository:**
   ```powershell
   git clone https://github.com/Peteroooooooo/local-onenote-mcp
   cd local-onenote-mcp
   ```
2. **Create the development environment with uv:**
   ```powershell
   uv sync --all-groups
   ```
3. **Start Codex or Claude Code from the repository root:**
   ```powershell
   codex
   # or
   claude
   ```
   The checked-in project MCP configurations start the local server through
   `uv run --locked local-onenote-mcp`:
   - Codex reads `.codex/config.toml` when the project is trusted.
   - Claude Code reads `.mcp.json`; it asks for approval before first using a project-scoped MCP server.
4. **Run the unit tests:**
   ```powershell
   uv run pytest
   ```

---

## OneMore & Markdown Integration

> 💡 **Crucial Note on Optionality & Power:**
> - **100% Optional:** The [OneMore](https://github.com/stevencohn/OneMore) desktop add-in is **not** a hard dependency. Without it, the server operates fully, allowing complete notebook hierarchy discovery, search, page reads, exports, navigation, and page creation/modification using plain text or raw HTML.
> - **A Formatting Powerhouse:** If installed, it unlocks a massive productivity boost. It binds to OneMore's high-performance `.NET Markdig` rendering pipeline. This lets AI agents write in **standard, clean Markdown** (including bold, italics, code snippets with styling, bulleted/numbered lists, headers, and blockquotes) and automatically converts them into **perfectly formatted, native OneNote components and structured tables**.

The server achieves this by querying and binding directly to OneMore's native `Markdig.Signed.dll` via the Windows Registry or standard program paths:
- `C:\Program Files\River\OneMoreAddIn\Markdig.Signed.dll`
- `C:\Program Files (x86)\River\OneMoreAddIn\Markdig.Signed.dll`

If installed in a custom location, specify the path in your configuration variables:
```toml
[mcp_servers.local-onenote.env]
LOCAL_ONENOTE_MARKDIG_DLL = "C:\\path\\to\\Markdig.Signed.dll"
```

---

## API & Tool Directory

The default profile exposes typed P0/P1 tools grouped into four logical categories. The complete parameter and return contract is in [`docs/design/tool_contracts.md`](docs/design/tool_contracts.md); the static fields are in [`docs/design/object_model.md`](docs/design/object_model.md).

### 1. Discovery & Content Inspection
* `health_check`: Get server version, python location, and active features.
* Symmetric `list_*` / `get_*` tools for notebooks, section groups, sections, and pages.
* `query_hierarchy` / `get_path` / `get_tree`: Query typed metadata and rebuild Page indentation trees.
* `get_page` returns metadata only; `get_page_text` / `get_page_xml` read content explicitly.
* `get_page_objects` / `get_binary_content`: Query and extract sub-elements (like tables, images, ink, or file attachment payloads).
* `search_pages`: Search an explicit Notebook, SectionGroup, or Section using either bounded local scan or OneNote index; there is no silent backend fallback.

### 2. Creation & Structural Edits
* `create_notebook` / `create_section` / `create_section_group`
* `create_page`: Create formatted pages via `plain`, `html`, or `markdown`.
* `update_page_title` / `append_to_page` / `replace_page_body`
* `add_image_to_page`: Add local images. Automatically infers native dimensions if only width or height is provided.
* `rename_section_group` / `rename_section` / `reorder_page`: Typed P1 structural edits with confirmation and read-back.
* `move_section`: Experimental same-Notebook move, protected by a separate switch and isolated validation procedure.

### 3. File & App Control
* `publish_object`: Export any notebook, section, or page to local PDF files.
* `navigate_to` / `navigate_to_url`: Instantly focus and jump the desktop UI to specific elements.
* `sync_notebook`: Trigger synchronization for a typed Notebook target.
* `close_notebook`: Close a confirmed Notebook; this is not Notebook deletion.

### 4. Deletion and Development Profile

* `delete_section_group` / `delete_section` / `delete_page`: Confirmed typed deletes; default destination is the OneNote recycle bin.
* Notebook deletion is not supported.
* Raw hierarchy/page XML mutations and legacy generic destructive tools are not registered by default. They require an explicit local development profile and still cannot bypass write/delete policy.

> **Identifier Resolution Protocol:**
> `resolve_identifier` and the compatible read-only hierarchy listing try identifiers in this priority:
> 1. Exact OneNote Object GUID (Recommended for automation)
> 2. Relative Hierarchy Path (e.g., `Personal/Quick Notes/My Section`)
> 3. Unique display name
>
> All mutations accept exact object IDs only and require current name/title and parent confirmation fields.

---

## Verification & Smoke Tests

Ensure everything is configured and operating as expected before starting:

```powershell
# 1. Run a read-only discovery verification
uv run python scripts\smoke_mcp.py

# Mutation validation is intentionally manual and isolated:
# docs/dev/isolated_mutation_validation.md
```

---

## Prompt Engineering & Markdown Example

Here is a typical markdown format that can be generated dynamically:

```markdown
# Project Launch Checklist

- **Project:** Triton Migration
- **Target Date:** 2026-07-01

## Immediate Tasks
- Define system architecture layout.
- Finalize local security boundary reviews.

## Roadmap & Milestones
| Milestone | Responsibility | Status |
| --- | --- | --- |
| Beta Deploy | Infrastructure | **In Progress** |
| Production Cutover | Operations | Pending |
```

---

## Limits & Boundaries

This server relies on the Windows COM API and is restricted to single-user, Windows-native environments. Section Move remains experimental until validated against the installed OneNote build in a disposable notebook. Page body replacement is multi-step and non-atomic. Writes and deletes remain disabled unless explicitly enabled in the server environment.
