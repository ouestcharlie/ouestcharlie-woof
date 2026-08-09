# ouestcharlie-woof — Claude Working Rules

## Exception Handling

Every `except` block must log the exception. Use `%s` against the `exc` variable — never a bare message.

- `_log.error(..., exc_info=True)` — operation failed (always include traceback for startup/session failures)
- `_log.warning(...)` — degraded/fallback (e.g. sidecar not stopping cleanly)
- `_log.debug(...)` — benign/expected (e.g. response not JSON, failed progress forward)
- `_log.exception(...)` — daemon thread crashes (full traceback needed)

Exceptions that are re-raised still need a log before the `raise`.

**Do not wrap startup/subprocess exceptions as `AgentError`** — tools like `get_summary` catch `AgentError` and swallow it as a null result. Non-`AgentError` exceptions propagate to FastMCP and surface visibly in MCP inspector. Only raise `AgentError` for tool-level failures (bad result, protocol errors), not for sidecar startup failures.

Woof runs as a stdio MCP server — unlogged exceptions are invisible.

## Python Style

- **No inline imports**: all `import` statements must be at the top of the file. Never place imports inside functions or test bodies.
- **Docstrings/comments describe requirements, not callers**: a class or function's docstring should state what it needs and what it does, not assume or describe the outside architecture that calls it (e.g. don't write "binding the port is `__main__.py`'s job" inside `McpServer`'s docstring — just document that `server_urls` is a required list of URLs). Keeps the module decoupled from any specific caller and avoids stale references when callers change.

## Testing

### Python (Woof server)
```
.venv/bin/pytest tests/ -v
```

### Linting

Use `uv tool run ruff check ...` (not bare `ruff` or `uv run ruff`) to lint Python files.

### JavaScript / Svelte (gallery)
```
cd gallery && npm test
```

Test files live next to the component they test: `src/components/Foo.svelte` → `src/components/Foo.test.js`.

**Layering** (keep pure/cross-cutting logic in `lib/`, not in components): test each concern once at its own altitude. `lib/` modules own the exhaustive cases (pure, no DOM); component tests prove only wiring — that the module's output reaches the DOM and callbacks fire — not the arithmetic the lib test already covers. Duplicating exhaustive cases in a component test makes it break in two places per change. See `gallery/README.md` (Architecture, Testing) for the full rationale. Run `npm run test:coverage` for a V8 report to spot untested branches (no enforced threshold).

**Patterns to follow** (see `IndexingProgress.test.js` as reference):
- Mock `fetch` per test with `vi.fn()` — return `{ ok: true, json: () => Promise.resolve(data) }`
- Use `waitFor` for all async assertions (component polls on mount)
- `@modelcontextprotocol/ext-apps` is mocked at the module level in `App.test.js`; for component tests pass a plain object with the needed methods as a prop
- `<details><summary>` splits text across nodes — query by `container.querySelector('details')` rather than `getByText`
- `getByText(/regex/)` fails when the same value appears twice (e.g. matching count in two rows) — use distinct values in test data or `getAllByText`
