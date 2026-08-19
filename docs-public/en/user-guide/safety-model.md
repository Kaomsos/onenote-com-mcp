# Safety Model and Limits

[简体中文](../../zh-CN/user-guide/safety-model.md) | [Documentation home](../../README.md)

The design goal is simple to state: **a misconfigured or misbehaving client should not be able to damage your notebooks.** Everything below serves that goal.

## Local-only boundary

- All OneNote access goes through the local COM API via a fixed PowerShell bridge. There is no Microsoft Graph, Azure, online OAuth, telemetry, or remote content processing.
- Binary `.one` files are never read or edited directly.
- Untrusted content is never interpolated into PowerShell source or command strings; the bridge uses structured JSON/temp-file transport.

## Fail-closed authorization

- Seven independent gates (Create, Writes, Deletes, Organize, Local File IO, UI Control, Notebook Lifecycle) all default to **off**. See [Configuration](configuration.md).
- Authorization is checked first, before any readiness probing or backend work. A policy rejection produces **zero** backend calls.
- The policy is fixed at server startup; nothing can expand permissions at runtime.
- Raw XML access, generic hierarchy mutation, and permanent-delete tools are not published in the production profile at all.

## Exact-ID mutations

- Every mutation targets an exact OneNote object ID plus an optimistic confirmation field (typically the object's last-known `modified` value). There is no silent fallback to name matching or broad targeting.
- Delete tools are always non-permanent: objects go to the OneNote recycle bin and stay recoverable from the OneNote UI.
- Move is reconstructive and strictly ordered: the copy is verified first; only then is the source deleted non-permanently. A failed or unverified copy never deletes the source.

## Readiness and effect prerequisites

- OneNote readiness means both a running `ONENOTE.EXE` process and a visible top-level window. Every authorized effect checks this after authorization and before any backend work; pure reads do not require it.
- `health_check` is always check-only. `launch_onenote_gui` is the single explicit recovery effect (UI Control gate), with at most one trusted process-launch request and bounded readiness observation.

## Bounded work

- Search, copy, and batch mutations run against explicit budgets. Budget exhaustion is a structured failure, never silent unbounded scanning.
- Batches (1–20 items) are preflighted as a whole, execute in input order, and stop at the first failed or uncertain item — no broad rollback, no mutation replay. Partial results preserve per-item states so you can inspect live state before recovering.

## Content-free audit

Logs and audit records capture operation names, success/failure, and timing — never notebook content, bridge payloads, secrets, or raw tool arguments.

## Verified-fidelity copy

Rich-object copy fidelity is allowlisted and evidence-bound: object types are only treated as losslessly copyable after real-backend validation. Unsupported or unverified objects fail closed instead of producing silently degraded copies. This fidelity contract covers the supported title/content/object/topology projection only; it excludes source revision/authorship markers and original creation/modification timestamps. See [copy content exclusions](../../../docs/lesson/copy_content_type_exclusions.md) for the current content boundary and the [product boundary](../../../docs/product/README.md) for metadata non-guarantees.

## Known limits

- Windows desktop, single-user local sessions only; no cloud or cross-process transaction boundary.
- Reparent stays within one notebook; cross-notebook container transfer uses reconstructive Move.
- Page-body replacement and recursive copy/move are multi-step and non-atomic.
- Copy/Move rebuild targets and do not preserve source revision markers or original creation/modification timestamps. OneNote may generate new target-owned metadata.
- External inbound links cannot retain identity across reconstructive copy/move (new IDs are created).
- OneNote may normalize standalone display-equation whitespace during COM writes ([observed limitation](../../../docs/lesson/display_equation_com_leading_whitespace_normalization.md)).
- Verified behavior is documented with its evidence scope. A pass on one OneNote/Office combination is not claimed as a guarantee for all versions.
