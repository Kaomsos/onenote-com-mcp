# Local OneNote MCP

A local Microsoft OneNote MCP server for Windows. It controls the OneNote desktop app through the local OneNote COM API—**requiring no Azure, Microsoft Graph, API keys, or online OAuth.**

---

## Design & Architecture

项目文档的分类、权威来源和 TODO 维护规则见 [`docs/README.md`](docs/README.md)；项目级待办统一收录在 [`docs/todo/README.md`](docs/todo/README.md)。

- **Local-Only Boundary:** Every operation executes directly through the local OneNote desktop installation. No data ever leaves your computer.
- **COM-First Engineering:** No direct binary `.one` file manipulation. All writes and reads leverage OneNote’s native COM engine, ensuring maximum data integrity and sync compatibility.
- **Safe Execution Bridge:** Inputs are passed safely through JSON-based temp files, completely avoiding PowerShell string interpolation or risk of command injections.
- **Typed Object Surface:** Stable Notebook, SectionGroup, Section, Page, and PageContentObject contracts with ID-only mutations.
- **Single Hierarchy Parser:** Complete hierarchy and Search fragments flow through one bridge-independent typed parser; legacy raw-attribute hierarchy models are removed.
- **Safe-by-Default Mutations:** Writes, deletes, permanent deletes, experimental reorder/move/copy operations, and raw development tools are independently disabled by default.
- **Index-only Search:** OneNote `FindPages` searches the root or one exact Notebook/SectionGroup/Section, with strict live-index pagination and candidate/snippet budgets.
- **COM Convergence & Reconciliation:** Mutations wait for two stable live observations, preserve typed HRESULT/retryability, and classify applied/partial/indeterminate state before returning.
- **Process-local Coordination:** Reads may share execution, while each mutation holds one exclusive lease from confirmation through COM execution and stable read-back; this does not claim a cross-process or Desktop-wide transaction.

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
        "LOCAL_ONENOTE_ENABLE_DELETES": "false",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION": "false",
        "LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY": "false",
        "LOCAL_ONENOTE_ENABLE_MOVE_PAGE": "false",
        "LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS": "false"
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
LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REORDER_SECTION = "false"
LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY = "false"
LOCAL_ONENOTE_ENABLE_MOVE_PAGE = "false"
LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS = "false"
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

The default profile exposes typed P0/P1 tools plus policy-gated P2 experimental tools. The complete parameter and return contract is in [`docs/design/tool_contracts.md`](docs/design/tool_contracts.md); the static fields are in [`docs/design/object_model.md`](docs/design/object_model.md).

### 1. Discovery & Content Inspection
* `health_check`: Fail closed before any COM hierarchy read unless an existing OneNote Desktop process has a visible GUI window; on success, return content-free Desktop readiness, server/Python locations, and active features. It never starts OneNote implicitly.
* Symmetric `list_*` / `get_*` tools for notebooks, section groups, sections, and pages. The five `list_*` tools remain available during the reviewed migration window; no removal is implied until the separate user approval gate is satisfied.
* `query_notebook` / `query_section_group` / `query_section` / `query_page`: Query one fixed hierarchy metadata type. Notebook Query is fixed to all currently open Notebooks; the other three require strict `scope={"mode":"root"}` or one exact allowed native start-node ID. `offset` / `page_size` paginate the filtered live hierarchy (default/max 200) and do not reduce `GetHierarchy` retrieval or metadata scanning. Page title Query never reads body text; use `search_pages` for content.
* `get_path` / `get_tree`: Read stable ancestry and rebuild Page indentation trees.
* `get_page` returns metadata only; `get_page_text` / `get_page_xml` read content explicitly.
* `get_page_objects` / `get_binary_content`: Query and extract sub-elements (like tables, images, ink, or file attachment payloads).
* `search_pages`: Search the OneNote index with required `scope={"mode":"root"}` or `scope={"mode":"start_node","start_node_id":"..."}`. A start node must be one exact open Notebook, SectionGroup, or Section ID. `offset` and `page_size` provide live-index pagination (default/max 200); the filtered candidate set is capped before slicing, and there is no local-scan fallback or public backend selector.

### 2. Creation & Structural Edits
* `create_notebook` / `create_section` / `create_section_group`
* `create_page`: Create formatted pages via `plain`, `html`, or `markdown`. Bounded Presentation MathML is accepted inside HTML: omit `display` for an inline equation or use `display="block"` for a standalone equation; unsupported MathML elements and attributes fail closed. Create read-back is bound to the COM-allocated ID, expected type, parent, and active state; a friendly-path remap is accepted only when it is unique and newly observed. Duplicate Page titles never select an earlier Page occurrence.
* `update_page_title` / `append_to_page` / `replace_page_body`
* `add_image_to_page`: Add local images. Automatically infers native dimensions if only width or height is provided.
* `rename_section_group` / `rename_section` / `reorder_page`: Typed P1 structural edits with confirmation and read-back.
* `reorder_section`: Typed same-parent, ID-preserving Section reorder with full sibling XML and Page fidelity checks; protected by its experimental policy.
* SectionGroup reorder is deliberately unsupported and must be rejected. The local OneNote backend exposes SectionGroup siblings in a fixed ascending name order; `UpdateHierarchy` may return success while ignoring a requested `A,C,B` element order, so neither Notebook-parent nor nested-parent SectionGroups have a supported reorder contract.
* `reparent_page` / `reparent_section` / `reparent_section_group` change a typed object's container parent within the same Notebook; they never cross Notebook boundaries and never mean Copy followed by source deletion. `reparent_page` defaults to the selected Page only: excluded descendants stay in the source Section and are promoted one level; pass `include_descendants=true` to reparent the complete indentation subtree. The selected Page becomes a root Page in the destination in either mode. All three use exact confirmation fields, bounded before/after verification, and the shared `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_REPARENT` gate in addition to Writes. Page calls report native Page/content-object ID remaps through `id_map`. Raw hierarchy XML is not exposed by any production profile.

### 3. File & App Control
* `publish_object`: Export any notebook, section, or page to local PDF files.
* `navigate_to` / `navigate_to_url`: Instantly focus and jump the desktop UI to specific elements.
* `sync_notebook`: Trigger synchronization for a typed Notebook target.
* `close_notebook`: Close a confirmed Notebook; this is not Notebook deletion.

### 4. Deletion and Development Profile

* `delete_section_group` / `delete_section` / `delete_page`: Confirmed typed deletes; default destination is the OneNote recycle bin.
* Notebook deletion is not supported.
* Raw Page XML mutations are not registered by default. The legacy generic `delete_hierarchy` tool has been removed from every production profile; use typed delete tools with exact IDs and confirmation fields. Remaining advanced operations require an explicit local development profile and still cannot bypass policy.

### 5. Experimental Copy & Reconstructive Move

* `plan_copy` plus typed `copy_page` / `copy_section` / `copy_section_group` / `copy_notebook` use a content-aware, stale-plan digest before any mutation. The digest binds stable Page content and object identities while excluding OneNote-owned clocks, selection/view state, and local cache/path metadata; raw Page XML hashes remain diagnostic evidence and do not make a plan stale by themselves.
* Every copied source maps to one fresh, distinct typed target ID before copied Page content or ordering is written. Success reports allocated and resolved targets; partial failures distinguish unresolved allocations, source/topology touch state, and manual recovery requirements.
* Every successful `reparent_*`, `copy_*`, and `move_*` execution returns top-level `destination_position`, an observed post-operation position for the response's target root. Page uses a zero-based index in the destination Section's complete flat Page sequence; Section and SectionGroup use same-type direct children of the final parent. Even when a Page subtree is included, only the target root position is returned and no Page level is included. Notebook Copy returns `status="not_applicable"`. Plan tools do not predict positions, and execution never performs an implicit Reorder to manufacture one.
* Page Copy and Page Move both default to the single selected Page. Pass `include_descendants=true` to the matching plan and execute tool only when the complete indentation subtree is intended; the option is bound into `plan_digest`. For a root-only Move, excluded descendants are bound by the plan and promoted one level before the selected source is recycled, so they remain active in the source Section. `destination_section_id` always names a Section rather than a parent Page: Copy preserves existing Page order, appends the newly allocated target block, normalizes its root to level 1, and restores selected descendant levels relative to that root. Section, SectionGroup, and Notebook Copy remain fully recursive. Copy never overwrites, merges, or auto-renames a conflicting target.
* The isolated `copy-page` regression suite exercises duplicate child titles in same-Section, cross-Section, and cross-Notebook destinations. Its RichText source also contains one inline and one standalone MathML equation; “standalone” means block MathML without visible residual text, not exclusive ownership of an `OE` or a non-empty predecessor `OE`. Each target must preserve the bounded equation projection and surrounding line layout, be fresh and disjoint from both sources and pre-existing same-title anchors, and leave anchor content and topology unchanged. User inspection of `run-2026-08-12-01-11-36` and v6 `run-2026-08-12-01-25-30` confirmed the equations themselves were retained but the display equation acquired a leading blank line. A later three-hop isolated run (`run-2026-08-12-10-45-04`) established the exact COM pattern: the same display-equation CDATA span gained one additional `<br/>` per reconstruction (`1 → 2 → 3 → 4`) while MathML remained identical. Copy now models standalone block MathML as `DisplayEquation`, removes the complete known-empty span/break wrapper before every outbound write, and admits at most one empty span containing one break on read-back. The fixed programmatic three-hop run `run-2026-08-12-11-28-08` passed all hops and held the sequence at `1 → 1 → 1`. Recipe v10 writes the block formula in a separate append and requires the initial COM read-back to contain the one observed empty `span + br`; its new fingerprint invalidates earlier `copy-page` cache entries, while the complete six-case topology suite remains a separate user-run regression.
* Unknown Page XML roots are omitted; an unknown descendant causes its containing top-level content block to be omitted. Both cases are returned as structured Copy issues rather than silently passed through.
* Validated Page content types are `Outline`, `Image`, `RichText`, `Table`, `List`, `Tag`, `DisplayEquation`, `InkDrawing`, `UIShape`, `MediaFile`, and `InsertedFile`. Stable rich content, MediaFile, and InsertedFile use strict canonical read-back. Inline bounded MathML uses `semantic_mathml`. A page containing standalone block MathML uses `semantic_display_equation`: equation count, display mode, element structure, token hashes, visible text, content objects, and binary hashes must match; the only additional read-back differences admitted are zero or one pure-empty `span + br` immediately before each display equation, the presence or whitespace serialization of a complete paired OneNote `<!--[if mathML]>...<![endif]-->` wrapper around the same verified MathML root, and COM-recomputed `Size.width/height` on an Outline whose sole authored content is exactly one block MathML root. Position and mixed-content Outline bounds remain strict. Two breaks, incomplete/unrelated comments, visible span text, unrelated markup, or MathML drift fail closed. An accepted documented COM normalization may still report `lossless=true`; that means semantic Copy fidelity under this contract, not byte-identical CDATA. List/Tag-only pages use a separate semantic tier. InkDrawing and UIShape use bounded geometry tiers (`1e-4` and `0.02`).
* `plan_move_page` / `move_page` implement Move by reconstruction: they create new Page IDs and issue non-permanent source deletes only after the defined Copy and topology checks pass. Success requires every selected source Page to disappear from the active hierarchy; a root-only Move additionally requires every excluded descendant to remain active with verified promoted topology and unchanged content. COM recycle-bin metadata is reported when available but is not an acceptance gate.
* `plan_move_section` / `move_section` and `plan_move_section_group` / `move_section_group` only accept a destination in another open Notebook. They copy the complete container subtree, require a complete injective `id_map` plus `copy_contract_satisfied=true`, revalidate the source, issue exactly one typed root `DeleteHierarchy(permanently=false)`, then require every original source subtree ID to be inactive and the destination snapshot to remain stable. Move never maintains a second content-type gate: any Copy accepted by the shared Copy contract is Move-eligible. Same-Notebook requests are rejected with a `reparent_*` recommendation.
* User-run isolated scenarios confirmed both Page Move ranges, the cross-Notebook Section/SectionGroup Move pipelines, and isolated InkDrawing/UIShape/MediaFile/InsertedFile Copy. The 2026-08-11 MediaFile run covered both same-Section and cross-Section video copies; the user then removed the source Section in OneNote and confirmed the retained copies still worked. The 2026-08-12 InsertedFile run passed strict canonical comparison, and the user opened the copied attachment and confirmed its synthetic content. These results widen only the static content-fidelity allowlist described above and do not remove the default-off experimental policy gates.
* Copy remains disabled unless `LOCAL_ONENOTE_ENABLE_EXPERIMENTAL_COPY=true`. Page Move additionally requires Deletes and `LOCAL_ONENOTE_ENABLE_MOVE_PAGE=true`; Section/SectionGroup Move uses the separate `LOCAL_ONENOTE_ENABLE_MOVE_CONTAINERS=true` gate. All switches default to false.
* Other content types remain unverified and prevent the shared Copy contract from authorizing source deletion. In particular, FileAttachment, MeetingInfo, and Embedded Spreadsheet (内嵌电子表格) are currently unsupported; Embedded Spreadsheet has no observed public `kind`/XML contract and must not be treated as Table, InsertedFile, or FileAttachment. See the recorded evidence boundary in [`docs/lesson/copy_content_type_exclusions.md`](docs/lesson/copy_content_type_exclusions.md).

For repeated local development of complex manual-validation fixtures, the runner supports an explicit, default-off `--use-cache` mode. It stores only closed disposable Notebook bytes under the ignored `.local-validation/fixture-cache/`, materializes a new working copy for every run, proves OneNote opened that working path rather than the immutable template, and performs fresh live validation before mutation. Managed cache, staging, and working paths use ordinary Windows absolute paths with a hard 240 UTF-16-unit preflight limit; full SHA-256 identities remain in metadata while disk locations use a 32-hex fingerprint key and typed `p` or `a/<hex>` instances. The project does not rely on Windows long-path opt-in or `\\?\` paths. Legacy payload is never read, migrated, or deleted by the new runtime; after the user runs the pre-upgrade human-gated `clear all`, only a proof-backed empty ownership shell may be stamped into the new schema. This validation cache is not part of the MCP server surface and never accepts user/business Notebooks.

Migration note (2026-08-10): callers that previously omitted a Page Copy scope received the full indentation subtree. Omission now means root Page only. To preserve the old behavior, create the plan with `include_descendants=true` and submit the same value to `copy_page`; reusing a digest with a different value is rejected before mutation.

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

# Every named scenario is a complete isolated suite. This dry-run must be
# reviewed by the user and does not access OneNote. Normal output is a compact
# policy/tool-count and lifecycle summary; add --json when the full fixture,
# allowlist, process budget, and exact lease plan must be reviewed:
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --verbosity normal

# Only the user may explicitly start the corresponding real scenario suite:
.venv\Scripts\python.exe tests\manual_validation\run.py rename --verbosity normal

# The create scenario also verifies two same-title Pages receive distinct IDs;
# default cleanup is exact and non-permanent:
.venv\Scripts\python.exe tests\manual_validation\run.py create --keep-notebook

# Every named action accepts --keep-worksite. It keeps the source Notebook open,
# preserves that action's verified post-mutation state, and records exact IDs plus
# manual cleanup guidance. For example, preserve both Page Copy scope targets for UI review:
.venv\Scripts\python.exe tests\manual_validation\run.py copy-page --keep-worksite

# DisplayEquation has its own non-interactive, programmatic three-hop Copy suite;
# it cleans up by default and never asks for bootstrap confirmation or UI verdict:
.venv\Scripts\python.exe tests\manual_validation\run.py copy-display-equation --dry-run --json

# Review every explicitly registered test-scenario plan serially. Exploratory
# validation scenarios are excluded until registered; all owns no shared run-dir:
.venv\Scripts\python.exe tests\manual_validation\run.py all --dry-run --verbosity normal

# The user may remove --dry-run to run isolated scenarios serially. Each child
# creates its own Notebook/run-dir; a failed child closes its exact leased
# Notebook bundle, and all continues only after that isolation is proven.
```

Named scenarios default to `--verbosity normal`; use `quiet` for phase-only output or
`verbose` for content-free mutation timing, convergence scalars, batched read counts,
policy/allowlist counts, and phase timings. Non-JSON `all` streams each serial child's
progress immediately with a scenario prefix; it no longer waits for a child to finish
before displaying that child's phases. Human-readable modes always end with a
compact result and artifact paths rather than the complete nested run result. `--json`
overrides verbosity and remains the only mode that prints the complete payload (one JSON
document for a named scenario, JSON Lines for `all`). Progress never prints tool
arguments, OneNote IDs, Page content, XML, binary data, queries, or full responses.
Named scenarios close every exact leased working Notebook after failure by default,
including when run alone. `--keep-notebook` or `--keep-worksite` explicitly preserves it
open; neither flag is accepted by `all`. A real `all` continues after a failed child only
when durable failure-finalization evidence proves the complete child bundle closed; a
missing proof or close failure stops the batch. Working files, evidence, and validated
cache templates remain preserved. Dry-run still checks the complete registry. A pure
cache activation `0x8004201D` gets one pre-mutation
close/reopen of the same working copy; cache templates are neither rebuilt nor discarded.

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

This server relies on the Windows COM API and is restricted to single-user, Windows-native environments. SectionGroup reorder is not supported because the backend provides a fixed ascending name order rather than a mutable sibling order. Reparent is restricted to hierarchy changes within one Notebook; Page, Section, and SectionGroup Reparent remain experimental typed capabilities. Move always copies and verifies the target before non-permanently deleting the source; it creates new Page IDs and cannot preserve external inbound links. Page body replacement and recursive Copy/Move are multi-step and non-atomic. Writes and deletes remain disabled unless explicitly enabled in the server environment.

Current content limitations are explicit:

* **Online Video cannot be copied losslessly.** Reconstruction can retain its preview image, visible text, binary image, and external link while losing the clickable/player binding. It therefore cannot satisfy the shared Copy contract, and reconstructive Move cannot authorize source deletion. See the [observed limitation](docs/lesson/online_video_copy_loses_player_semantics.md).
* **Every tested COM write of a standalone equation creates leading whitespace.** In the currently validated OneNote/Office environment, the first `UpdatePageContent` write of Presentation MathML with `display="block"` produces one pure-empty `span + br` before the equation. Submitting that wrapper again adds another break. This applies to COM-backed `create_page`, `append_to_page`, `replace_page_body`, Copy, and cross-Notebook reconstructive Move. Copy removes the precisely known wrapper before each outbound reconstruction to prevent accumulation, but OneNote writes one wrapper back; the server therefore does not promise the complete absence of a leading blank or byte-identical XML/CDATA. Inline equations are not covered. See the [evidence and environment boundary](docs/lesson/display_equation_com_leading_whitespace_normalization.md).
* **`MeetingInfo` remains unverified.** It has no dedicated fixture, comparator, or real-backend fidelity result. If encountered, it fails closed and cannot satisfy Copy or authorize Move source deletion.
* **Rich object creation remains narrower than Copy/Move.** The server has no validated production creation contract for `InsertedFile`, `InkDrawing` (sometimes shortened to InkDraw), `UIShape`, or `MediaFile`; it does not claim that it can programmatically author these objects. All four types have real-backend reconstruction Copy evidence and may participate in Move through the shared verified Copy gate. For `InsertedFile`, this support is reconstruction-only and requires a readable local `pathSource`, `pathCache`, or legacy `path`; it does not imply a typed creation API or make `FileAttachment` an alias.
