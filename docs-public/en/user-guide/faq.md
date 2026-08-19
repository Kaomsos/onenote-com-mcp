# FAQ and Troubleshooting

[简体中文](../../zh-CN/user-guide/faq.md) | [Documentation home](../../README.md)

## General

**Does this work with OneNote for the web / OneNote UWP / macOS?**
No. The server requires Microsoft OneNote Desktop on Windows, because it depends on the OneNote COM API. The legacy Windows 10 UWP app does not expose COM.

**Does any note content leave my machine?**
No. All access is local COM. There is no Graph, Azure, OAuth, telemetry, or remote processing. Audit logs are content-free.

**Can it edit `.one` files directly?**
No, and it never will by design. All reads and writes go through the OneNote application via COM.

**Do I need OneMore?**
Only if you want rich Markdown compiled into OneNote HTML during page creation/append. Without OneMore, plain text and validated HTML remain available. A custom assembly location can be set with `LOCAL_ONENOTE_MARKDIG_DLL`.

## Setup problems

**`health_check` reports not ready.**
Readiness requires both a running `ONENOTE.EXE` and a visible top-level OneNote window. Start OneNote Desktop manually and keep it visible, or enable the `UI Control` gate and call `launch_onenote_gui()`, then `health_check` again. The server never launches OneNote implicitly.

**A mutation tool returns a policy error.**
The corresponding authorization gate is off (the default). Set the relevant `LOCAL_ONENOTE_ENABLE_*` variable to `true` in your MCP client configuration and restart the client. Check gate combinations in [Configuration](configuration.md) — e.g. page creation needs Create + Writes; Move needs Create + Writes + Deletes.

**I changed an environment variable and nothing happened.**
The policy is read once at server startup. Restart the MCP client (which respawns the server).

**Tools time out on large notebooks.**
Raise `LOCAL_ONENOTE_MCP_TIMEOUT` (seconds) and, for clients with their own limits, the client-side tool timeout. Budget-related rejections are structured errors, not timeouts — see the next question.

**A batch or search fails with a budget error.**
That is intentional bounded work, not a malfunction. Narrow the scope (target a smaller subtree, fewer items, shorter content), or raise the specific budget variable listed in [Configuration](configuration.md).

## Behavior questions

**Why did Move give my page a new ID?**
Move is reconstructive: verified copy, then non-permanent source delete. New objects get new IDs. External links to the old object cannot retain identity.

**Does Copy/Move preserve revision markers and original timestamps?**
No. Copy/Move rebuilds the target and deliberately does not carry forward source revision/authorship markers or original creation/modification timestamps. OneNote may generate target-owned values. A `lossless=true` result only covers the documented supported title/content/object/topology projection.

**A delete happened — can I get the object back?**
Public deletes are always non-permanent. Look in the OneNote recycle bin (Notebook → Deleted Notes). Permanent-delete tools are not published.

**Why does `search_pages` miss a page I just wrote?**
`search_pages` uses OneNote's live index, which updates asynchronously. Recently written content may not be indexed yet. Query tools (hierarchy metadata) are not index-dependent.

**Why was my copy rejected for an unsupported object?**
Copy fidelity is allowlisted and evidence-bound: object types are only accepted after real-backend validation proved lossless round-trips. Unverified types fail closed rather than degrade silently. Current boundary: [copy content exclusions](../../../docs/lesson/copy_content_type_exclusions.md).

**Why can't I reorder section groups?**
The observed backend exposes a fixed name order for section groups rather than a stable mutable sibling order, so the capability is deliberately unsupported and rejected.

**`request_notebook_sync` returned ok but the notebook isn't synced.**
The tool proves the sync request was accepted, not that synchronization completed. Visible sync activity is also not completion — this is an observed OneNote behavior.

## Reporting problems

Open a GitHub issue with reproduction steps, your Windows/OneNote Desktop edition, and the structured error envelope if available. **Never paste notebook content, real object IDs, or personal file paths into a public issue.**
