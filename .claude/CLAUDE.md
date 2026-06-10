# ouestcharlie-woof — Claude Working Rules

## Exception Handling

Every `except` block must log the exception. Use `%s` against the `exc` variable — never a bare message.

- `_log.error(..., exc_info=True)` — operation failed (always include traceback for startup/session failures)
- `_log.warning(...)` — degraded/fallback (e.g. sidecar not stopping cleanly)
- `_log.debug(...)` — benign/expected (e.g. response not JSON, failed progress forward)
- `_log.exception(...)` — daemon thread crashes (full traceback needed)

Exceptions that are re-raised still need a log before the `raise`.

**Do not wrap startup/subprocess exceptions as `AgentError`** — tools like `get_partition_summaries` catch `AgentError` and swallow it as a null result. Non-`AgentError` exceptions propagate to FastMCP and surface visibly in MCP inspector. Only raise `AgentError` for tool-level failures (bad result, protocol errors), not for sidecar startup failures.

Woof runs as a stdio MCP server — unlogged exceptions are invisible.

## Testing

### Python (Woof server)
```
.venv/bin/pytest tests/ -v
```

### JavaScript / Svelte (gallery)
```
cd gallery && npm test
```

Test files live next to the component they test: `src/components/Foo.svelte` → `src/components/Foo.test.js`.

**Patterns to follow** (see `IndexingProgress.test.js` as reference):
- Mock `fetch` per test with `vi.fn()` — return `{ ok: true, json: () => Promise.resolve(data) }`
- Use `waitFor` for all async assertions (component polls on mount)
- `@modelcontextprotocol/ext-apps` is mocked at the module level in `App.test.js`; for component tests pass a plain object with the needed methods as a prop
- `<details><summary>` splits text across nodes — query by `container.querySelector('details')` rather than `getByText`
- `getByText(/regex/)` fails when the same value appears twice (e.g. matching count in two rows) — use distinct values in test data or `getAllByText`
