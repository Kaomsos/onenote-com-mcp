# Repository instructions

## Documentation governance

- Before adding, moving, or substantially changing documentation, read [`docs/README.md`](docs/README.md).
- Project TODOs live under [`docs/todo/`](docs/todo/README.md). Do not leave standalone `TODO_*.md` files in feature or development directories.
- Every TODO document uses an immutable three-digit prefix and must be listed in `docs/todo/README.md` with its status and priority.
- When a TODO changes status, priority, title, or path, update its index row in the same change.
- Preserve historical TODO numbers; do not renumber files or reuse identifiers after completion or cancellation.

## Non-read-only tool verification

- Any tool whose real execution requires a mutation-policy permission (writes, deletes, permanent deletes, experimental mutations, raw XML, or a future non-read-only permission) must use semi-automated manual validation. Mocked contract tests may run automatically, but they do not replace a real isolated validation.
- Put the real-backend scenario under [`tests/manual_isolated/`](tests/manual_isolated/), expose it through the single manual runner entry point, and document the exact user command in that directory's README. Do not create an independent implicit or batch mutation entry point.
- The user must explicitly start one named scenario. The runner must derive a static least-privilege child-process policy, verify it with `health_check` before the tool call, use exact IDs and fresh confirmation fields, and never require a second permission flag or interactive confirmation after startup.
- Real mutation scenarios must target dedicated disposable OneNote data and capture before/after evidence. Recoverable operations must restore and verify the original state; non-restorable operations must be constrained to manifest-allowlisted disposable targets and clearly report the remaining state.
- Never invoke real non-read-only scenarios from default pytest, CI, hooks, installation/package scripts, imports, timers, watchers, or background agents. A new or changed non-read-only tool is not considered real-backend verified until its manual scenario and usage documentation are updated and a user confirms the isolated run.
