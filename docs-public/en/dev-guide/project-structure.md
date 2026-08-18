# Project Structure

[简体中文](../../zh-CN/dev-guide/project-structure.md) | [Documentation home](../../README.md)

## Repository layout

```text
onenote-com-mcp/
├─ src/local_onenote_mcp/     Production MCP server
│  ├─ domain/                 Typed domain objects (transport/COM independent)
│  ├─ page/                   Page parsing, formatting, building, images, copy semantics
│  ├─ services/               Application orchestration; policy, exact-ID, budgets
│  ├─ tools/                  Thin MCP adapters over services
│  ├─ bridge.py               Trusted local COM boundary (PowerShell, JSON transport)
│  ├─ server.py / settings.py / policy.py   Composition and process-level configuration
│  └─ operation_catalog.py / tool_surface.py  Canonical operation registry and tool surface
├─ tests/                     Deterministic automated tests (mock/contract level)
│  └─ manual_validation/      HUMAN-GATED real-backend validation framework
├─ docs/                      Maintainer docs: design contracts, dev workflows, lessons, TODO ledger
├─ docs-public/               This public documentation (bilingual)
├─ scripts/                   Read-only smoke test and diagnostics
├─ bin/                       npm launcher entry
└─ pyproject.toml / package.json / uv.lock
```

## Architecture layers

The production code enforces a strict layering, documented authoritatively in [architecture](../../../docs/design/architecture.md) and [Operation Runtime](../../../docs/design/operation_runtime.md):

- **`domain/`** defines typed domain objects (Notebook, SectionGroup, Section, Page, PageContentObject) and stays independent of MCP transport, subprocess execution, and OneNote COM access.
- **`page/`** owns page XML semantics: parsing, formatting, building, images, and copy-oriented projections. XML handling is centralized and covered by round-trip/invariant tests.
- **`services/`** is the orchestration layer and the primary enforcement boundary for policy, exact-ID targeting, confirmation fields, budgets, and recoverable failure behavior.
- **`tools/`** adapts MCP inputs/outputs to services. Tool functions stay thin, typed, and consistent with the documented response envelope; they never re-implement service logic.
- **`bridge.py`** is the trusted local COM boundary. It uses structured JSON/temp-file transport and never interpolates untrusted content into PowerShell source or command strings.
- **`server.py`, `settings.py`, `policy.py`** own composition and process-level configuration. Environment reading is centralized; there are no hidden alternative registration paths.

## The Operation Registry

A canonical **53-operation Registry** owns, for every public tool: exposure, category, authorization, independent platform preflight policy, execution strategy, handler, audit, and retry semantics. Reads share a process-local lease; mutation and lifecycle effects use exclusive coordination through preflight, execution, reconciliation, and stable read-back.

A handful of internal/incubating capabilities (`resolve_identifier`, `get_page_xml`, `navigate_to_url`, `get_special_locations`, `get_parent`) are deliberately non-registered: no environment switch exposes them.

## Documentation map

Maintainer documentation lives under [`docs/`](../../../docs/README.md) and remains the authoritative source:

| Directory | Responsibility |
| --- | --- |
| `docs/design/` | Current architecture, object model, parser boundaries, and tool contracts |
| `docs/dev/` | Development, validation, and troubleshooting workflows |
| `docs/lesson/` | Reusable engineering lessons with explicit evidence boundaries |
| `docs/overview/` | Time-scoped research and assessment reports |
| `docs/todo/` | Project TODO ledger with immutable IDs and status discipline |

This public documentation (`docs-public/`) summarizes and links into those sources; it never forks contract detail into a second competing authority.
