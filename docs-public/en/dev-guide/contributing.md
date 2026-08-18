# Contributing

[简体中文](../../zh-CN/dev-guide/contributing.md) | [Documentation home](../../README.md)

Thank you for considering a contribution. This project welcomes issues and pull requests, within a few firm boundaries that exist to protect users' notebooks.

## Ground rules

1. **Never run real OneNote mutation scenarios from automation.** Agents, pytest, CI, hooks, timers, watchers, and background tasks are forbidden from executing a real `tests/manual_validation/run.py <scenario>`. Only a human user starts real runs. `--dry-run` variants are always safe.
2. **Keep the local-only boundary.** Contributions must not introduce cloud APIs, telemetry, remote content processing, or direct `.one` file editing.
3. **Keep defaults fail-closed.** New mutation capabilities need their own independent, default-off authorization gate when their risk differs from existing gates.
4. **Never include user data.** No notebook content, real object IDs, personal paths, or machine identifiers in issues, PRs, tests, fixtures, or documentation.
5. **Follow the layered `AGENTS.md` files.** They are binding for humans and AI agents; the closest file to what you are editing applies, and more specific files only tighten the rules. See [Engineering rules](engineering-rules.md).

## Development setup

```powershell
git clone https://github.com/Peteroooooooo/local-onenote-mcp
cd local-onenote-mcp
uv sync --all-groups
uv run pytest
```

The automated suite is deterministic and runs without OneNote installed. See [Automated testing](testing.md).

## Making a change

1. Read the nearest `AGENTS.md` before editing files in a scope.
2. Iterate with the smallest relevant test files; run the full pure suite (`uv run pytest`) before submitting.
3. If your change touches a public tool contract (name, parameters, response shape, policy, environment variables), update the implementation, tests, `docs/design/`, and user-facing README/docs content **in the same change**.
4. If your change adds or modifies a non-read-only tool, also add a named manual-validation scenario (static policy/allowlist, isolated fixture, before/after evidence, failure handoff) and document the exact user command. Real execution is handed to a maintainer/user — see [Manual validation framework](manual-validation.md).
5. Keep the bilingual public docs (`docs-public/en/` and `docs-public/zh-CN/`) synchronized when you change them.

## Pull request expectations

- Describe the change, its impact scope, and its compatibility implications.
- State which tests you added/updated and the result of the full pure suite.
- Call out any permission-gate or contract change explicitly.
- Redact all sensitive information; PR contents are public.
- Real-backend claims require user-confirmed evidence; otherwise mark them as pending validation.

## Reporting issues

- **Bugs:** include reproduction steps, Windows/OneNote Desktop edition, your gate configuration, and the structured error envelope if available. Never paste notebook content.
- **Feature proposals:** describe the use case and, for mutations, how the capability stays exact-ID, bounded, and fail-closed.
- **Security:** until a dedicated security policy is published, report suspected vulnerabilities through GitHub issues without including notebook content or personal data.
