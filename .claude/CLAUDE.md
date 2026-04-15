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

```
.venv/Scripts/python -m pytest tests/ -v
```
