# Manual Validation Framework

[简体中文](../../zh-CN/dev-guide/manual-validation.md) | [Documentation home](../../README.md)

Automated tests mock the COM boundary, so they can prove code contracts but not real OneNote behavior. Every mutation capability therefore has a second, human-gated verification layer: **named real-backend scenarios** under `tests/manual_validation/`, run against real OneNote Desktop in fully isolated disposable notebooks.

This chapter explains the framework's design for contributors. The operational authority is [`tests/manual_validation/README.md`](../../../tests/manual_validation/README.md), and the binding rules are its [`AGENTS.md`](../../../tests/manual_validation/AGENTS.md); the internal architecture is documented in [scenario/fixture architecture](../../../docs/design/manual_validation_scenario_fixture_architecture.md).

## Why real runs are human-gated

Real scenarios mutate a real OneNote backend. Even though every scenario is isolated to disposable data, the framework treats "who may pull the trigger" as a hard security boundary:

- **Only the user starts real runs.** Agents, pytest, CI, hooks, package/install scripts, imports, timers, watchers, and background tasks must never execute a real `run.py <scenario>` or `run.py all`.
- Automation may modify validation code, run pure contract tests, inspect saved evidence read-only, and run anything explicitly carrying `--dry-run`.
- A result is only reportable as "passed on the real backend" when the user personally ran it and provided or confirmed the evidence. Mocks and dry-runs are never sufficient.

This protects against exactly the failure mode that makes agentic development risky: an eager automation loop silently exercising write capabilities against a user's real application state.

## The scenario model

The public CLI is flat: `run.py <scenario>` is one complete isolated suite. There are no helper subcommands; `all` is the only batch entry, and `clear` is the only maintenance group.

```powershell
# Inspect the plan first — dry-run creates no directories, starts no MCP, never touches OneNote
.venv\Scripts\python.exe tests\manual_validation\run.py rename --dry-run --json

# Real run: user only
.venv\Scripts\python.exe tests\manual_validation\run.py rename
```

Each real scenario run is a complete isolation loop:

```text
create a fresh isolated notebook (narrow lifecycle wrapper)
→ start exactly one scenario-scoped MCP subprocess
→ build only this scenario's fixture, run exactly the selected scenario
→ write local evidence report
→ close the exact leased notebook (default) or keep it open on request
```

Key properties:

- **Registry-driven.** Every public scenario is one named `Scenario` class registered in a single `SCENARIO_REGISTRY`; `scenarios/__init__.py` is the explicit ordered manifest. There is no filesystem discovery. Inclusion in the `all` batch (`included_in_all`) is an explicit, reviewed decision per scenario.
- **Static least privilege.** Each scenario starts at most one MCP subprocess with a frozen policy and tool allowlist — the minimal closure needed for its fixture, mutation, evidence read-back, and restore/cleanup. Permissions are verified against `health_check` before the fixture is created and can never grow after startup. Permissions of different scenarios are never merged.
- **Disposable fixtures, exact IDs.** Every run creates a fresh run-scoped notebook bundle (never user data), addresses every mutation by exact object ID plus confirmation field, and performs bounded work only.
- **Before/after evidence.** Scenarios capture before snapshots, mutation responses, after snapshots, restore proofs, and content-free audit trails under `.local-validation/run-<timestamp>/`. Failures fail closed: no further mutations, evidence preserved, and the leased notebook closed precisely by default.
- **Restore by default.** Recoverable operations restore and verify original state. Deletes stay non-permanent. `--keep-worksite` (explicit, off by default) preserves a verified worksite for manual UI inspection and records exact IDs plus manual cleanup notes.

## Fixture recipes and the template cache

Each scenario owns exactly one **fixture recipe** — a declarative, fingerprinted description of the notebook structure and content it needs. Fresh runs build the fixture live and validate it before mutating.

Because rich fixtures are expensive to rebuild, an explicitly opted-in cache exists: `--use-cache` materializes a new working copy from a closed, immutable, previously validated template (an opaque byte-for-byte copy — never parsed, never opened as the template itself). Materialized copies are re-validated live before any mutation. Without `--use-cache`, scenarios perform zero cache operations.

Interactive scenarios (`interactive-<operation>`) extend this for content that must be human-authored (ink, shapes, media recordings): a fresh run chains a human-gated bootstrap phase where the user authors synthetic content and gives a run-bound verdict, then publishes the validated template for later cached reuse.

## `all` and `clear`

- `run.py all` serially launches the explicitly included scenarios as fully independent subcommands — no shared run directory, notebook, MCP process, policy, fixture, or evidence. After a failed child, the batch only continues if the child proved that all of its notebook leases were precisely closed.
- `run.py clear runs|cache|all` is the only maintenance entry for deleting historical validation artifacts and cache entries. Real execution is interactive-only: it must be started by the user in a foreground terminal and confirmed by typing an action-bound value at a prompt (there is no `--confirm` flag; non-interactive stdin is rejected). Deletion is limited to precisely owned payloads under the fixed `.local-validation/` root, guarded by ownership metadata, containment checks, reparse-point rejection, a live snapshot of currently open OneNote paths, and per-target receipts. Automation may only run its `--dry-run`.

## What contributors must do

Any new or changed non-read-only production tool requires, in the same change:

1. automated contract coverage (mocked/isolated), and
2. a named scenario here with static policy/allowlist, isolated fixture, before/after evidence, failure handoff, and the exact user command documented in the manual-validation README.

Then hand the real command to the user. Never run it yourself, and never mark the work complete without user-confirmed real evidence.
