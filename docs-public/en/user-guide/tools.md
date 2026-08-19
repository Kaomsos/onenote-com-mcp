# Tool Overview

[简体中文](../../zh-CN/user-guide/tools.md) | [Documentation home](../../README.md)

The production profile exposes exactly **53 tools**, organized by user task. The authoritative parameter, scope, response, effect, budget, and authorization contract is [tool contracts](../../../docs/design/tool_contracts.md); this page is an orientation map.

## Catalog

| Category | Tools | Gate(s) |
| --- | --- | --- |
| Session | `health_check`, `launch_onenote_gui` | — / UI Control |
| Hierarchy Browse | `list_notebooks`, `get_hierarchy_path`, `expand_notebook`, `expand_section_group`, `expand_section`, `expand_page`, `expand_hierarchy` | — |
| Metadata Get | `get_notebook_metadata`, `get_section_group_metadata`, `get_section_metadata`, `get_page_metadata` | — |
| Query & Search | `query_notebook`, `query_section_group`, `query_section`, `query_page`, `search_pages` | — |
| Page Content Read | `get_page_text`, `get_page_content_objects`, `get_page_content_object_binary` | — |
| Hyperlink | `get_hyperlink` | — |
| Create | `create_notebook`, `create_section_group`, `create_section`, `create_page` | Create (+ Writes for pages) |
| Rename | `rename_page`, `rename_section_group`, `rename_section` | Writes |
| Reorder | `reorder_page`, `reorder_section`, `sort_children` | Writes |
| Organize | `reparent_page`, `reparent_section`, `reparent_section_group` | Writes + Organize |
| Page Content Mutation | `append_page_content`, `add_page_image_from_file`, `replace_page_body`, `delete_page_content_object` | Writes (replace/delete also Deletes; image also Local File IO) |
| Recoverable Delete | `delete_page`, `delete_section`, `delete_section_group` | Deletes |
| Copy | `copy_page`, `copy_section`, `copy_section_group`, `copy_notebook` | Create + Writes |
| Reconstructive Move | `move_page`, `move_section`, `move_section_group` | Create + Writes + Deletes |
| Export | `export_object_to_pdf` | Local File IO |
| UI Navigation | `navigate_to` | UI Control |
| Notebook Lifecycle | `request_notebook_sync`, `close_notebook` | Notebook Lifecycle |

There are no compatibility aliases for renamed tools, and no environment switch exposes additional internal capabilities.

## Response envelope

Every public call returns one of these exact shapes:

```json
{"ok":true,"result":{},"warnings":[],"execution":{}}
```

```json
{"ok":false,"error":{"code":"...","message":"...","details":{}},"execution":{}}
```

## Behavior worth knowing

- **`health_check` first.** Call it at the start of every session. It never launches OneNote. Every authorized effect independently requires an existing visible OneNote GUI; pure reads do not.
- **Query vs. Search.** Query tools read hierarchy metadata; `search_pages` uses OneNote's live index for page body discovery. Both return candidates — mutations still require an exact ID.
- **Typed expand parameters** name their object type (`notebook_id`, `section_group_id`, `section_id`, `page_id`).
- **`get_page_text`** defaults to bounded `sanitized_html_v1`, which preserves reviewed formatting, links, lists, tags, tables, and canonical Presentation MathML without exposing raw page XML or binary payloads. Use `mode="plain"` for the legacy `{text, chars}` projection.
- **Page editing is operation-based, not revision-based.** The public surface supports append, whole-body replace, exact content-object delete, and image insertion. It does not expose revision history, track changes, revision-ID rollback, or arbitrary text-range patches. `expected_modified` is an optimistic concurrency confirmation, not a revision selector.
- **Page scope** is the boolean `include_subpages` (default `false`): `false` targets only the page and protects/promotes excluded descendants where needed; `true` targets the complete indentation subtree.
- **Bounded batches.** `create_*`, `rename_*`, `reparent_*`, and recoverable `delete_*` accept either single-item fields or a bounded `items` batch (1–20). A batch is preflighted as a whole, executes in input order, stops on the first failed or uncertain item, and never performs broad rollback or replay. Partial responses preserve `applied` / `failed` / `not_attempted` item states.
- **`sort_children`** stably sorts a confirmed parent's complete direct-child sequence by `name`, `created`, or `modified`. Child type is inferred: Notebook/SectionGroup sorts sections; Section/Page sorts pages. Leveled pages move as complete indentation blocks; SectionGroups are never sorted.
- **Copy and Move are single reconstructive calls.** Planning stays inside the operation. Page copy/move preserves the exact logical page title — including `/`, `\`, `:`, repeated spaces, `%`, `~`, and Unicode — unless `destination_title` is explicitly supplied. Copy results include per-page verification stages and typed failure categories. Source revision/authorship markers and original creation/modification timestamps are not carried forward; OneNote may generate target-owned values. `verified`, `lossless`, and `copy_contract_satisfied` do not promise fidelity for that metadata.
- **Move is reconstructive:** a verified copy followed by a non-permanent source delete. It creates new IDs and can return partial or indeterminate results that must not be blindly retried.
- **`export_object_to_pdf`** only writes a new PDF and never overwrites an existing path.
- **`request_notebook_sync`** proves the request was accepted, not that synchronization completed.
- **SectionGroup reorder is unsupported** because the observed backend exposes a fixed name order rather than a stable mutable sibling order.
