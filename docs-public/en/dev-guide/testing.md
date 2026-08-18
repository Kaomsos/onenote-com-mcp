# Automated Testing

[简体中文](../../zh-CN/dev-guide/testing.md) | [Documentation home](../../README.md)

## Test layers

The project separates verification into three layers with strictly different trust levels:

| Layer | Location | Runs where | Touches real OneNote? |
| --- | --- | --- | --- |
| Pure automated tests | `tests/` | Anywhere (CI-safe) | Never |
| Manual-validation contract tests | `tests/manual_validation/tests/` | Anywhere (CI-safe) | Never |
| Real-backend scenarios | `tests/manual_validation/run.py` | User's machine, started by the user only | Yes — isolated disposable notebooks |

This page covers the first two. The third is documented in [Manual validation framework](manual-validation.md).

## Running the automated suite

```powershell
.venv\Scripts\python.exe -m pytest -q
```

or, with uv:

```powershell
uv run pytest
```

The default suite is deterministic, local-only, and safe to run without OneNote installed or open. Tests never modify real notebooks, never start real manual-validation scenarios, and never depend on user documents.

## Design rules for tests

- **Mock at the bridge.** Bridge and mutation behavior are tested with fakes, monkeypatching, and minimal documented fixtures plus contract-level assertions. A test's pass/fail must never depend on current OneNote state.
- **Cover safety invariants, not just features:** policy rejections, exact-ID targeting, confirmation fields, bounded work, content-free logging, partial failure, restore/cleanup, and stable response structures.
- **Test fail-closed behavior explicitly.** Production permissions, validation gates, and error handling are never weakened to satisfy a mock.
- **`write_contract` marker** identifies mocked/isolated mutation contract tests. It does not authorize real mutations and does not substitute for user-run manual validation.
- Platform-specific assumptions are isolated and mocked so the suite runs in ordinary development environments.

## What automated tests can and cannot prove

Mocks, pytest, and `--dry-run` outputs prove **code contracts and orchestration**. They cannot prove real OneNote COM behavior. Real-backend evidence only comes from named manual-validation scenarios explicitly run by the user; documentation must never report a real scenario as passed based on automated results alone.

## Smoke test

A read-only transport smoke test is available from a checkout:

```powershell
uv run python scripts\smoke_mcp.py --tools-only   # validates the 53-tool list, no OneNote connection
uv run python scripts\smoke_mcp.py                # read-only probes; needs a visible OneNote Desktop
```
