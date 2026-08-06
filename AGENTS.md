# Repository instructions

## Documentation governance

- Before adding, moving, or substantially changing documentation, read [`docs/README.md`](docs/README.md).
- Project TODOs live under [`docs/todo/`](docs/todo/README.md). Do not leave standalone `TODO_*.md` files in feature or development directories.
- Every TODO document uses an immutable three-digit prefix and must be listed in `docs/todo/README.md` with its status and priority.
- When a TODO changes status, priority, title, or path, update its index row in the same change.
- Preserve historical TODO numbers; do not renumber files or reuse identifiers after completion or cancellation.

## Non-read-only tool verification

- Any tool whose real execution requires a mutation-policy permission (writes, deletes, permanent deletes, experimental mutations, raw XML, or a future non-read-only permission) must use semi-automated manual validation. Mocked contract tests may run automatically, but they do not replace a real isolated validation.
- Put the real-backend scenario under [`tests/manual_validation/`](tests/manual_validation/), expose it through the single manual runner entry point, and document the exact user command in that directory's README. Do not create an independent helper action, implicit, aggregate, or batch mutation entry point. Every public `run.py <scenario>` command is itself a complete isolated suite: it must create a fresh Notebook, prepare fixtures, run exactly that named scenario, report evidence, and apply the requested close/keep lifecycle. `create` is the one fixture-only scenario; `validate`, `inspect`, `read`, and `report` must not be public CLI actions.
- The user must explicitly start one named `run.py <scenario>` suite. Each scenario may start at most one MCP child process. Its static least-privilege policy and tool allowlist must cover only that scenario's fixture, mutation, evidence reads, and restore/cleanup closure, and must be verified once with `health_check` before fixture creation. Do not combine permissions across scenarios or expand them at runtime. Source Notebook create/get/close may use only the narrow lifecycle wrapper and its exact ID/name/path lease; fixture creation must remain inside the scenario MCP process. Never require a second permission flag or interactive confirmation after startup.
- Real mutation scenarios must target dedicated disposable OneNote data and capture before/after evidence. Recoverable operations must restore and verify the original state; non-restorable operations must be constrained to manifest-allowlisted disposable targets and clearly report the remaining state.
- Never invoke real non-read-only scenarios from default pytest, CI, hooks, installation/package scripts, imports, timers, watchers, foreground agents, or background agents. Agents may edit the runner, execute pure/mock contract tests, and print a command for the user, but must not run a real `run.py <scenario>` suite on the user's behalf. A new or changed non-read-only tool is not considered real-backend verified until its manual scenario and usage documentation are updated and a user confirms the isolated run.
