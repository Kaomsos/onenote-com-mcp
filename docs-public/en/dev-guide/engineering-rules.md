# Engineering Rules

[简体中文](../../zh-CN/dev-guide/engineering-rules.md) | [Documentation home](../../README.md)

The repository is governed by layered `AGENTS.md` files (repository root, `src/`, `tests/`, `tests/manual_validation/`, `docs/` and subdirectories). They bind human contributors and AI agents alike. This page is a public summary of the rules you must know before contributing; the `AGENTS.md` files themselves are authoritative, and more specific files may tighten — never loosen — the safety gates.

## Non-negotiable safety gates

1. **Local-only boundary.** No cloud APIs, telemetry, remote content processing, or direct `.one` file editing without an explicit project-level decision.
2. **Fail-closed permissions.** Writes, deletes, permanent deletes, experimental mutations, reconstructive Move, and raw XML stay behind mutually independent gates that default to off. New mutation capabilities with different risk profiles get their own independent gate.
3. **Exact-ID, typed mutations.** No name-based mutation targeting, no unbounded hierarchy scans, no ad-hoc raw XML paths.
4. **Real mutation validation is human-gated.** Agents, pytest, CI, hooks, package/install scripts, imports, timers, watchers, and background tasks must never run a real `run.py <scenario>` — only the user starts real runs. See [Manual validation framework](manual-validation.md).
5. **No destructive convenience.** User data and unrelated worktree changes are protected; destructive Git or filesystem operations are never used to simplify an implementation.

## Contract discipline

Public tool names, parameters, response structures, policy requirements, and environment variables are treated as contracts. When a contract changes, the same change must update:

- the implementation,
- the automated tests,
- the current design docs under `docs/design/`,
- and user-facing README/documentation content.

There are no compatibility aliases for renamed tools and no hidden environment switches.

## Production code rules (`src/`)

- Never bypass `MutationPolicy` checks.
- Mutations use exact object IDs plus current confirmation fields; no silent fallback to name matching.
- Search and copy work is bounded by configured budgets; budget exhaustion is an explicit failure.
- Never log OneNote content, bridge payloads, secrets, or raw tool arguments — audits stay content-free.
- New or changed non-read-only tools additionally require an isolated named scenario under `tests/manual_validation/`, with real execution left to the user.

## Documentation rules (`docs/`)

- Current behavior lives in `docs/design/`; workflows in `docs/dev/`; evidence-bounded lessons in `docs/lesson/`; open work in `docs/todo/` (immutable IDs, synchronized index).
- Never claim a real OneNote scenario passed based on mocks, dry-runs, or agent inference alone — only user-confirmed evidence counts as a real backend result.
- Documentation moves/renames must update all repository-relative links in the same change.

## Verification baseline

Run the smallest relevant pure tests while iterating; run the full suite before delivering cross-cutting changes:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Real OneNote acceptance commands are always handed to the user; they are outside the scope of automated verification.
