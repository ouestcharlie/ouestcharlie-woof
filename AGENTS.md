# OuEstCharlie Woof — Agent Working Rules

Rules for AI coding agents working in this repo. Claude Code reads them through the root
`CLAUDE.md`, which imports this file. Project-wide rules and the high-level design are in the
[ouestcharlie repo](https://github.com/ouestcharlie/ouestcharlie) (its `AGENTS.md` and
`design/` folder).

## Project Context

Woof is the OuEstCharlie controller: an MCP server for the AI assistant, an MCP client that
launches and calls the agents (Whitebeard, Wally), and the gallery UI served as an MCP App.

Woof runs as two programs (see [woof_LLD.md §2](doc/design/woof_LLD.md#2-process-lifecycle--bootstrap-woof-bridge--daemon)):

- `woof-bridge` (`src/woof/bridge.py`): the command the MCP host starts, one per connection.
  It speaks stdio to the host and relays to the daemon over HTTP, starting the daemon if
  it is not running.
- `woof` (`python -m woof`): the single daemon that does the work. It serves MCP over
  Streamable HTTP, the gallery and media on one loopback port, and runs in the background
  with no terminal.

| Part | Folder | Language |
|---|---|---|
| Woof bridge and daemon | `src/woof/` | Python |
| Gallery (MCP App) | `gallery/` | Svelte / JavaScript |
| Claude plugin and skills | `.claude-plugin/`, `plugins/` | — |

The agents and the toolkit come from the `ouestcharlie` repo (PyPI packages, or editable
path sources for development).

## Architecture Understanding Required

Before making changes, check the design documents in [doc/design/](doc/design/):
- [woof_LLR.md](doc/design/woof_LLR.md) — Low-level requirements
- [woof_LLD.md](doc/design/woof_LLD.md) — Low-level design
- [woof_LLD_rationale.md](doc/design/woof_LLD_rationale.md) — Design decisions & alternatives

## Code Guidelines

### General
- Write all documentation, comments, and user-facing text in American English (e.g., "color" not "colour", "organize" not "organise").
- **All imports at the top of the file**: never place imports inside functions or test bodies.
- **Docstrings/comments describe requirements, not callers**: a class or function's docstring should state what it needs and what it does, not assume or describe the outside architecture that calls it (e.g. don't write "binding the port is `__main__.py`'s job" inside `McpServer`'s docstring — just document that `server_urls` is a required list of URLs). Keeps the module decoupled from any specific caller and avoids stale references when callers change.

### Python (Woof server)

- **Tests**: use the project's own venv, never `python`, `python3`, or `uv run pytest`:
  ```bash
  .venv/bin/pytest tests/ -v
  ```
- **Lint**: ruff is a dev dependency, `ruff~=0.17.0` in `pyproject.toml` (the same range as
  the `ouestcharlie` repo). The exact version is locked in `uv.lock`, and it is the one the
  pre-commit hook runs. Use the venv's copy, never `uvx ruff` / `uv tool run ruff` or a
  global `ruff` (another version, results may differ from the hook):
  ```bash
  .venv/bin/ruff check .
  .venv/bin/ruff format --check .
  ```
  Patch releases come in with a lock refresh (`uv lock --upgrade-package ruff`). Moving to
  a new minor version (0.18) is a deliberate change: update the range and reformat in the
  same PR, here and in the `ouestcharlie` repo.

### Exception handling (Python)

Every `except` block must log the exception. Use `%s` against the `exc` variable — never a bare message.

- `_log.error(..., exc_info=True)` — operation failed (always include traceback for startup/session failures)
- `_log.warning(...)` — degraded/fallback (e.g. sidecar not stopping cleanly)
- `_log.debug(...)` — benign/expected (e.g. response not JSON, failed progress forward)
- `_log.exception(...)` — daemon thread crashes (full traceback needed)

Exceptions that are re-raised still need a log before the `raise`.

**Do not wrap startup/subprocess exceptions as `AgentError`** — tools like `get_summary` catch `AgentError` and swallow it as a null result. Non-`AgentError` exceptions propagate to FastMCP and surface visibly in MCP inspector. Only raise `AgentError` for tool-level failures (bad result, protocol errors), not for sidecar startup failures.

The daemon runs in the background with no terminal, and the host only sees the bridge:
an exception that is not logged is invisible. Its log is the only record.

### JavaScript / Svelte (gallery)

- **No inline JS**: Never use inline `<script>` tags or `onclick`/`onX` attributes in HTML. All JavaScript must live in separate `.js` files and be referenced via `<script src="...">`.
- **Tests**:
  ```bash
  cd gallery && npm test
  ```
  Test files live next to the component they test: `src/components/Foo.svelte` → `src/components/Foo.test.js`.
- **Layering** (keep pure/cross-cutting logic in `lib/`, not in components): test each concern once at its own altitude. `lib/` modules own the exhaustive cases (pure, no DOM); component tests prove only wiring — that the module's output reaches the DOM and callbacks fire — not the arithmetic the lib test already covers. Duplicating exhaustive cases in a component test makes it break in two places per change. See `gallery/README.md` (Architecture, Testing) for the full rationale. Run `npm run test:coverage` for a V8 report to spot untested branches (no enforced threshold).
- **Test patterns to follow** (see `IndexingProgress.test.js` as reference):
  - Mock `fetch` per test with `vi.fn()` — return `{ ok: true, json: () => Promise.resolve(data) }`
  - Use `waitFor` for all async assertions (component polls on mount)
  - `@modelcontextprotocol/ext-apps` is mocked at the module level in `App.test.js`; for component tests pass a plain object with the needed methods as a prop
  - `<details><summary>` splits text across nodes — query by `container.querySelector('details')` rather than `getByText`
  - `getByText(/regex/)` fails when the same value appears twice (e.g. matching count in two rows) — use distinct values in test data or `getAllByText`

### Documentation
- **Don't reference issue numbers in code or docs**: Issue numbers (e.g. "OEC-39") are only valid at the time of their implementation and become stale afterwards. Explain the rationale directly instead of pointing to an issue.
- **Links**: relative paths within this repo, full GitHub URLs to other repos.
