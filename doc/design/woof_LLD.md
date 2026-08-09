# Woof Low-Level Design

This document details the internal design of Woof. For requirements, see [woof_LLR.md](woof_LLR.md). For MCP tool definitions, see [controller_api.json](https://github.com/ouestcharlie/ouestcharlie/blob/master/controller_api.json). For the gallery frontend's build, architecture, and testing conventions, see [gallery/README.md](../../gallery/README.md).

## 1. Overview

### 1.1 Implementation status

Woof V1 implements the core search/browse/index loop. Sections marked **[Planned]** describe requirements from `woof_LLR.md` that are not yet implemented.

### 1.2 Structure of Woof

Woof runs on the user's device as a single persistent background daemon. The MCP host is the conversational UI shell; Woof is the security and operational boundary between it and the OuEstCharlie ecosystem. This document is organized around one foundational domain and three runtime domains:

- **§2 Process Lifecycle & Bootstrap** — how Woof comes into existence: the host launches a thin `woof-bridge` proxy, which discovers or lazily spawns the one long-lived daemon that everything else runs inside.
- **§3 Domain A — MCP Tools (Claude-facing)** — an MCP server exposing OuEstCharlie operations as tools the assistant invokes.
- **§4 Domain B — MCP App (gallery + its own server)** — an interactive gallery served as an MCP App resource (a sandboxed iframe), backed by a local HTTP server that streams media and session data.
- **§5 Domain C — Agent Controller (agent-facing)** — an MCP *client* that launches and supervises agent child processes.

```
MCP host (Claude Desktop, CoWork, Goose, …)
  │  spawns one stdio subprocess per connection
  ▼
woof-bridge  (stdio↔HTTP pump, one per host connection)     ── §2
  │  relays MCP JSON-RPC over HTTP; lazily starts the daemon
  ▼
Woof daemon  (single persistent uvicorn instance, 127.0.0.1:<ephemeral>)
  ├── Domain A: MCP tools at /mcp ── search, browse, index, configure …   ── §3
  ├── Domain B: MCP App (gallery iframe) + local HTTP routes (session + media)  ── §4
  ├── Configuration (~/.ouestcharlie/), discovery file + startup lock
  └── Domain C: Agent controller (MCP client to agents)                    ── §5
        ├── Whitebeard — indexing agent (ephemeral stdio child)
        └── Wally — consumption agent (persistent HTTP sidecar) → serves media
```

### 1.3 How the runtime domains connect

Domains A and B are deliberately decoupled — their only coupling is two pieces of shared state, both created in one domain and consumed in another:

- **Session tokens.** Domain A's search tools store match data in memory and return an opaque token; Domain B's HTTP server serves that data when the iframe requests it. Large match payloads never travel back through the MCP channel.
- **MCP App resource references.** Domain A's `browse_gallery` and `index_library` tools are registered with an `AppConfig` pointing at the gallery resource, which is what causes the host to open the iframe and push the tool result into it.

The two session managers (§4.2) are where these seams live: Domain A writes gallery sessions and Domain C writes indexing sessions, both served by Domain B. All three domains run inside the single daemon established in §2.

## 2. Process Lifecycle & Bootstrap (woof-bridge + daemon)

Woof is never launched directly by the host. The Desktop Extension manifest points the host at **`woof-bridge`** (`mcp_config.command` runs `uv tool run … woof-bridge`), a thin stdio↔HTTP proxy. This decouples the many short-lived per-connection stdio processes a host spawns from the single long-lived daemon that holds all state and does the work.

### 2.1 Bridge / daemon split

- The host spawns one `woof-bridge` stdio subprocess **per MCP connection** (a GUI host such as CoWork may open several concurrently).
- Each bridge relays MCP JSON-RPC **unmodified** between its stdio and the daemon's `/mcp/` endpoint — a transport-level pump between two `SessionMessage` stream pairs, with no protocol re-implementation and no Node/`mcp-remote` dependency (pure Python on the MCP SDK primitives Woof already uses). Malformed transport-layer messages are logged and dropped, since raw pumping has no protocol-level party to report them to.
- Exactly one Woof **daemon** runs: a single persistent uvicorn instance serving MCP (`/mcp`), the gallery, and media routes on one ephemeral loopback port. It outlives individual host connections. It runs in HTTP mode (`WOOF_TRANSPORT=http`); the bridge is the only stdio party.

### 2.2 Discovery & single-instance startup

Coordination state lives in two files under `platformdirs.user_config_dir("ouestcharlie")`:

- `woof-discovery.json` — `{pid, port, token}`, mode 0600, written atomically by the daemon once it is ready to serve.
- `woof-discovery.lock` — an advisory `filelock` guarding the check-then-spawn sequence.

`ensure_woof_running()` (bridge side) acquires the startup lock (bounded, 10 s), reads the discovery file, and probes it; if live, it returns immediately. Otherwise it removes any stale file and spawns a **detached** `python -m woof` (`WOOF_TRANSPORT=http`, `start_new_session` on POSIX / `DETACHED_PROCESS` on Windows), capturing early stdout/stderr to `woof-spawn.log` — so an import-time crash before the daemon's own logging takes over is not lost to `DEVNULL` — then waits up to 15 s for a fresh discovery file. The lock serializes concurrent bridges so they never spawn competing daemons; a bridge that times out acquiring the lock falls back to waiting for the other's instance rather than failing.

`probe_alive()` is two-layered: a cheap pid-liveness check (`is_pid_alive`, platform-specific — a Windows process-handle query rather than `os.kill(pid, 0)`, which is unsafe there; a non-blocking zombie reap first on POSIX) **plus** an authenticated `GET /healthz`. The HTTP check catches the case where the recorded pid/port has been reused by an unrelated process.

### 2.3 Transport & authentication

The daemon generates a 32-byte bearer token at startup (`generate_token`) and records it in the discovery file. Every daemon endpoint — `/mcp`, `/healthz`, `/keepalive`, `/shutdown`, and the media/session routes — requires `Authorization: Bearer <token>`. Because the port is ephemeral loopback and the token is readable only from the 0600 discovery file, no other local process can drive Woof. The bridge reads the token from discovery and attaches it to every relayed request and to the streamable-HTTP client it opens against `/mcp/` (requested with the canonical trailing slash to avoid a 307 redirect off Starlette's mount).

### 2.4 Idle shutdown & termination

The daemon self-terminates when quiet. `ActivityTracker.touch()` is called on every proxied request and bridge keepalive; `watch_idle()` polls `idle_seconds()` and, once past the idle timeout with all connections gone, requests shutdown once. Each bridge posts `/keepalive` every 60 s for as long as the host keeps it open, so an active connection keeps the daemon alive even without traffic. The daemon also stops on an authenticated `POST /shutdown` (what `woof-bridge --stop` sends) or on a signal, and removes the discovery file on exit. `watch_idle` is dependency-injected (the idle *decision* is separated from the uvicorn server) so it is unit-testable without a real server or wall-clock timeouts.

### 2.5 Startup configuration (`config.py`)

`WoofConfig` is loaded at daemon startup and holds the library definitions used by every domain. It is serialized once, by `to_dict()` on `LibraryConfig`, for both `WOOF_BACKEND_CONFIG` (agent env) and MCP tool responses; the `status` key is added by `server.py`, not `LibraryConfig`, so the config stays unaware of MCP concerns.

**LanceDB index location on Windows UNC paths.** `object_store` (the Rust storage layer under Lance) is unreliable on UNC paths and mapped network drives. When a library root resolves to a UNC share on Windows, the LanceDB index is redirected to `%LOCALAPPDATA%\ouestcharlie\indexes\<library_name>` so it stays on local NTFS. `_resolve_to_unc` uses `Path.resolve()` first (as `LocalBackend._resolve()` does), then falls back to `WNetGetUniversalNameW` for mapped drives whose resolved anchor is not already `\\`. The ctypes import is lazy and gated on `sys.platform == "win32"`. `_migrate()` backfills `lancedb_index_path` for existing UNC libraries on the first daemon start after upgrade.

## 3. Domain A — MCP Tools (Claude-facing)

Woof exposes OuEstCharlie capabilities as MCP tools. The assistant calls them in response to user requests; errors are logged in the Woof process before being returned.

### 3.1 Registered tools

- **Library management**: `index_library` (see §3.2).
- **Search and browse**: `search_photos`, `browse_gallery` (returns an MCP App reference), `get_summary` (aggregate stats, optionally scoped by the same filter syntax as `search_photos`).
- **Configuration**: `add_backend`, `list_backends`, `list_search_fields`.

### 3.2 Non-blocking `index_library`

`index_library` is non-blocking: it launches Whitebeard as a background `asyncio.Task` (via Domain C, §5.3), returns immediately with `{type:"indexing", session_id, serverUrl, …}`, and opens the gallery MCP App in indexing mode (progress bar + final summary pushed back to model context). `force_full_index=True` re-processes the entire library.

It is registered with the same `app=AppConfig(resource_uri=_GALLERY_URI)` as `browse_gallery`, so the one gallery resource is reused for both gallery and indexing modes; the iframe distinguishes them by the `type` field in the tool result (§4.3).

### 3.3 Search → gallery handoff

The gallery display uses a two-step flow to avoid passing large match payloads back through the assistant as tool arguments (which would produce excessive `tool-input-partial` MCP notifications):

1. **`search_photos`** calls Wally (with optional `sort_by`, `sort_order`), stores the first Wally page of matches (≤ 500) in an in-memory session keyed by a random `session_token`, and returns only lightweight statistics:
   ```json
   {
     "session_token": "<22-char opaque token>",
     "totalCount": 5432,
     "errors": 0,
     "errorDetails": []
   }
   ```
   The MCP client never paginates — it only hands the `session_token` to `browse_gallery`. Page navigation is entirely a gallery/HTTP concern, so `search_photos` neither takes a `page` argument nor returns `page`/`pageSize`/`hasMore`. The stored `queryContext` (library, Wally args, current page, pageSize) is what lets the gallery load additional server pages on demand; its `args` carry no `page` key, because `GallerySession.fetch_page` supplies the target page per request.

2. **`browse_gallery`** receives one or more `session_token` values, looks up the sessions, merges them (deduplicating by `contentHash`), and returns the combined match list to the gallery iframe via the MCP App tool-result mechanism. Merging a single token whose session has a `queryContext` inherits that context so the gallery can still paginate Wally pages; multi-session merges drop `queryContext`.

The stored session schema and its manager are described in §4.2 (the state is created here but served by Domain B).

### 3.4 Opening the MCP App

`browse_gallery` and `index_library` are registered with `app=AppConfig(resource_uri=_GALLERY_URI)`. This causes the host to open the gallery MCP App resource and push the tool result into it via postMessage. The result's `type` field selects gallery vs. indexing mode in the frontend.

### 3.5 Surfacing errors to Claude

Agent errors in the indexing and search paths are logged at `ERROR` level via the `woof.mcp_server` logger before being returned to the assistant as `{"error": "..."}` dicts, so failures are visible in the Woof process log even when the assistant summarizes them briefly. See §6 for planned error categorization.

## 4. Domain B — MCP App (gallery + its server)

The gallery is a Svelte application (compiled to vanilla JS, bundled with Vite) served by Woof as an MCP App resource and rendered inside the host's conversation as a sandboxed iframe. This domain has three parts: the local HTTP server that backs it (§4.1), the session state that server exposes (§4.2), and the frontend behavior (§4.3).

### 4.1 Local HTTP server (the App backend)

The daemon's HTTP server (§2) is bound to `127.0.0.1` on the ephemeral port recorded in the discovery file; its URL is also communicated to the iframe via the MCP App tool result. It binds loopback only — no external network access — and photo data never passes through the MCP channel.

| Route | Method | Handler |
|---|---|---|
| `/mcp` | — | MCP endpoint (Domain A), token-authenticated (§2.3) |
| `/gallery/{token}` | GET | Gallery HTML (MCP App) |
| `/gallery-static/{path}` | GET | Vite-built JS/CSS assets from `dist/` |
| `/api/results/{token}` | GET | JSON session data (matches + metadata) |
| `/api/results/{token}/page/{page}` | GET | Load 0-indexed Wally page into the session, return updated session JSON |
| `/api/indexing/{session_id}` | GET | JSON indexing-session state (`status`, `progress`, `total`, `message`, `summary`, `error`) |
| `/api/indexing/{session_id}/cancel` | POST | Request cancellation; transitions status to `cancelling` (409 if not cancellable) |
| `/thumbnail/{library_name}/{partition}/{avif_hash}` | GET | Proxied to Wally (AVIF tile grid) |
| `/previews/{library_name}/{partition}/{content_hash}.jpg` | GET | Proxied to Wally (on-demand JPEG) |
| `/video/{library_name}/{partition}/{content_hash}.mp4` | GET | Proxied to Wally, forwarding Range/Content-Range for seeking |
| `/healthz`, `/keepalive`, `/shutdown` | GET/POST | Lifecycle endpoints (§2.2–§2.4) |

`{partition}` may contain slashes (e.g. `2024/2024-07`).

**Runtime model.** The HTTP server (uvicorn/Starlette) runs as an `asyncio` task on the **same event loop as the MCP server** (FastMCP). The task is started inside `McpServer._lifespan` via `asyncio.create_task(serve_in_loop(...))` and cancelled on MCP shutdown. `McpServer.__init__` binds the HTTP socket synchronously (before `mcp.run()`), so `server_url` is known at construction time and can be embedded in tool results before the loop starts. Because both servers share one OS thread, **any synchronous work in a request handler that runs longer than ~1 ms must be offloaded via `loop.run_in_executor`** — blocking the loop stalls HTTP responses and MCP message processing simultaneously. `fetch_page_fn` (the callback that triggers a Wally server-page fetch) is an async coroutine awaited directly in `api_page` — no thread-pool bridge is needed.

**Media proxy.** All media requests (`/thumbnail/…`, `/previews/…`, `/video/…`) are forwarded to Wally's HTTP server via the single route `/{kind}/{library}/{rest:path}`. Woof has no direct access to backend storage — it is a pure proxy, keeping the storage abstraction entirely within Wally and enabling a future remote backend with no Woof changes. The `{library}` segment identifies which Wally sidecar to route to via `AgentClient.get_wally_connection(library_name)`, which returns `(http_port, token)`; both are discovered dynamically on every request so sidecar restarts are picked up automatically. The proxy streams bodies and forwards Range/Content-Range so `<video>` seeking works. If the named library's sidecar is not yet running, Woof returns `503`.

### 4.2 Session state layer (the seam)

Two in-memory managers hold the state the HTTP server serves. Neither is persisted across daemon restarts. This is where Domains A and C hand work to Domain B.

**`GallerySessionManager`** — created by Domain A's search tools (§3.3), read by `/api/results/*`. Each session holds at most one Wally page of matches at a time; the gallery requests additional pages via `/api/results/{token}/page/{page}`. Matches are stored in arrival order — sort is applied at the LanceDB level (descending `date_taken` by default) before results reach Woof. Capacity is 100 sessions; the oldest is evicted at capacity. Schema:

```json
{
  "matches":      [{ "…photo/video match record": "…", "library": "…" }],
  "querySummary": "Nikon photos, 2024",
  "totalCount":   5432,
  "queryContext": {
    "library_name": "kDrive Photos",
    "args":         { "root": "", "sort_by": "date_taken", "sort_order": "desc" },
    "page":         0,
    "pageSize":     500
  }
}
```
`queryContext` is `null` for sessions created by multi-session `browse_gallery` merges (they cannot paginate Wally pages).

**`IndexingSessionManager`** — written by Domain C's background-task callbacks (§5.3), read by `/api/indexing/*`. Tracks background Whitebeard runs keyed by a random `session_id` (URL-safe, 16 bytes). Capacity is 20 sessions (indexing runs are infrequent); the oldest is evicted at capacity. Access needs no locking: the HTTP server shares FastMCP's event loop, so the manager — shared by reference between MCP tool callbacks and HTTP handlers — is only ever touched on one OS thread. Session shape:

```json
{
  "session_id":      "…",
  "library_name":    "kDrive Photos",
  "partition_scope": ["2024/2024-07"],
  "status":          "running | cancelling | cancelled | completed | failed",
  "progress":        42.0,
  "total":           1234.0,
  "message":         "Indexing 2024/07… (42/1234)",
  "summary":         null,
  "error":           null,
  "started_at":      "2026-05-28T10:31:00+00:00"
}
```

| Method | Effect |
|---|---|
| `start(library_name, partition_scope) → session_id` | Creates a `running` session, returns its id |
| `update(session_id, progress, total, message)` | Updates progress fields; no-op if unknown |
| `complete(session_id, summary)` | Sets `status="completed"`, stores summary dict |
| `fail(session_id, error)` | Sets `status="failed"`, stores error string |
| `register_task(session_id, task)` | Associates the run's `asyncio.Task` so it can be cancelled |
| `cancel(session_id)` | Sets `status="cancelling"` and cancels the task; false if unknown or not `running` |
| `cancelled(session_id)` | Sets `status="cancelled"` (called when the task's `CancelledError` is observed) |
| `get(session_id) → dict \| None` | Returns the session dict, or `None` |

### 4.3 Gallery frontend (Svelte app)

The gallery is built with Vite (Svelte compiled to vanilla JS). Practical how-to — dev/build/test commands, running coverage, adding a locale — lives in [gallery/README.md](../../gallery/README.md); the architecture, conventions, and behavior are below.

**Architecture — smart root, dumb children.** `App.svelte` owns all view/selection state and passes data + callback props down to the components (`MediaGrid`, `PreviewPanel`, `IndexingProgress`). There is no store tree and no `createEventDispatcher` — idiomatic Svelte 5 runes. The guiding rule (from an audit that found logic buried in components): **pure or cross-cutting logic lives in `lib/`, not inside a component** — a component renders and wires; anything testable in isolation (URL builders, MCP-session bootstrap, pagination math, formatters, host-size reporting) belongs one level down as its own module, documented at the source. When adding a view, reach for the module that already owns a concern rather than inlining a testable helper or a second host-communication strategy.

**Testing conventions.** Tests are colocated (`Foo.svelte` → `Foo.test.js`). Each concern is tested **once, at its own altitude**: `lib/` modules carry the exhaustive cases (pure, no DOM); component tests prove only *wiring* — that a module's output reaches the DOM and callbacks fire — not the arithmetic the lib test already covers, so an algorithm change breaks one place, not two. Coverage (`npm run test:coverage`, V8 provider) is a spotlight for untested branches, not a gate — there is no enforced threshold.

**Rendering & progressive media loading.** The grid (`MediaGrid`) shows AVIF thumbnail tiles clipped from Wally's tile grid; video tiles carry a play-badge overlay. Opening an item shows the `PreviewPanel`: the container is pre-sized from the item's `width`/`height` (no layout shift), the previously-shown full image stays visible while the next JPEG loads, a spinner appears only if loading exceeds ~300 ms, and the incoming image crossfades in on `load`. Videos render a `<video>` (user-initiated playback, no autoplay) using the cover-frame JPEG as poster and streaming from `/video/…`; the detail panel shows codec, duration, and a warning when the browser cannot decode the source codec. Thumbnails and previews are fetched from the local HTTP server (§4.1); the iframe CSP restricts `img-src` to that loopback origin.

**Bidirectional flow.**
- Gallery → Woof: tool calls for search, navigation, album actions (via postMessage/MCP App protocol).
- Woof → Gallery: fresh results pushed as search completes; indexing progress updates.

**Pagination (double-layer).** Two coordinated layers:
- *Display pages* — `MediaGrid` renders 3 rows × viewport-width columns; the display page size is derived from the viewport (typically 12–20 items).
- *Server pages* — each session holds one Wally page (≤ 500 items). The total display-page count is **the sum of per-server-page ceilings** — `Σ ceil(pageSize_i / displayPageSize)` across the `pageMap` — not `ceil(totalCount / displayPageSize)`, because a display page never spans a server-page boundary (partial last pages round up independently). Counters show "Page X / Y" with absolute item indices across all server pages. This math is a pure module (`lib/pagination.js`); see the gallery README.
- Navigating past the last display page of the current server page calls `/api/results/{token}/page/{N}` to load the next server page (replacing the session's matches) before advancing; the reverse works for Previous. Prev/Next controls render above and below the grid and are hidden when the total is a single display page.
- Merged multi-search sessions have no `queryContext` and cannot cross server-page boundaries; navigation is bounded to the loaded matches.

**Indexing mode.** When `ontoolresult` receives `type === 'indexing'`, the frontend renders `IndexingProgress` instead of the grid. It:
1. Polls `/api/indexing/{session_id}` every second, updating the progress bar and message.
2. Stops polling once `status` leaves `running`/`cancelling`.
3. On `completed`: formats the summary from in-component state, calls `mcpApp.updateModelContext({content:[{type:'text', text: summaryMarkdown}]})` so the model has the result next turn, then `mcpApp.sendMessage` to trigger a new turn.
4. On `failed`: displays the error and calls `updateModelContext` with an error message.
5. Offers a Stop control that POSTs to `/api/indexing/{session_id}/cancel`; the resulting `cancelling`/`cancelled` status is user-initiated and produces no model turn.

When `mcpApp` is null (standalone `?sessionId=` dev path), the MCP callbacks are skipped and the summary is shown inline only.

**Fullscreen & loading.** Fullscreen is toggled via `mcpApp.requestDisplayMode()` (and Escape exits it). A shimmer skeleton grid shows while `loading` is true (between MCP App connection and the first tool result).

**Theming.** The gallery uses the host's MCP App design tokens (`--color-text-*`, `--color-background-*`, `--color-border-*`, …) exclusively — no hardcoded colors. Two layers ensure correct colors everywhere:
1. **Inside the AI host**: on `connect()` and every `onhostcontextchanged`, the gallery calls `applyDocumentTheme(ctx.theme)` and `applyHostStyleVariables(ctx.styles.variables)` from the `@modelcontextprotocol/ext-apps` SDK, writing host token values as inline styles on `<html>` (which beat any stylesheet).
2. **Standalone (dev server / `?token=` path)**: `src/global.css` defines the same token names on `:root` with canonical hex values and a `prefers-color-scheme: dark` block. These are no-ops once the host has applied its own inline values.

**Localization (design view).** The active locale is host-driven and in-memory: it is resolved from `HostContext.locale` (via the same `getHostContext()`/`onhostcontextchanged` channel as theme), falling back to `navigator.language` then the base locale, and applied without any reload. The `globalVariable` compile strategy is required **because the gallery is a sandboxed iframe** — the default cookie/URL strategies would navigate or reload the frame to switch language, which is not possible here. Only static UI chrome is localized; photo metadata, Wally's `querySummary`, and model-facing text (indexing summary markdown, `sendMessage` triggers) are assistant/data content and stay in English. Catalogue format, supported locales, and how to add one live in [gallery/README.md](../../gallery/README.md).

## 5. Domain C — Agent Controller (agent-facing MCP client)

Woof acts as an MCP client to agents. Each agent is an MCP server exposing its capabilities as tools.

### 5.1 Transport

- **stdio** (default): Woof launches agents as child processes and communicates over stdin/stdout — no port management, network exposure, or auth layer; the OS process boundary provides isolation.
- **Streamable HTTP**: for agents running as separate processes or containers, Woof connects via HTTP to a configurable MCP endpoint.

### 5.2 Agent lifecycle: ephemeral vs. persistent

| Agent | Lifecycle | Reason |
|---|---|---|
| Whitebeard | Ephemeral — spawned per tool call, exits on completion | Indexing is infrequent; no persistent server needed |
| Wally | Persistent sidecar — started on first search, kept alive for the Woof session | Wally's HTTP preview server must stay up to serve media between tool calls |

`AgentClient` manages the Wally sidecar via a dedicated `asyncio` session task backed by an `asyncio.Queue`. All tool calls to Wally are posted to this queue and executed serially inside the session task, which owns all context managers (`httpx`, `streamable_http_client`, `ClientSession`). This avoids "exit cancel scope in different task" errors from `anyio`. The sidecar is started lazily on the first tool call for a given backend.

### 5.3 Background agent tasks

`AgentClient.call_tool_background()` wraps `_call_ephemeral` in an `asyncio.Task` and returns immediately, without blocking the MCP event loop:

```python
def call_tool_background(
    self, module, tool_name, args, library, *,
    on_progress, on_complete, on_error,
) -> asyncio.Task
```

The internal coroutine awaits `_call_ephemeral` and dispatches to the callbacks:
- `on_progress(progress, total, message)` — per MCP progress notification from the agent
- `on_complete(result)` — once, when the tool returns successfully
- `on_error(exc)` — if the tool raises `AgentError` or any unexpected exception; the exception is logged before the callback runs

All callbacks are plain synchronous functions invoked on the MCP event loop (no thread-pool dispatch). `index_library` (§3.2) wires these directly to `IndexingSessionManager.update/complete/fail` (§4.2) — this is the write path for the indexing state Domain B serves.

### 5.4 Agent launch & Wally port discovery

When Woof launches an agent as a child process (stdio), it passes backend credentials and scope as environment variables, then performs the MCP `initialize` handshake over stdio, receiving the agent's tool definitions and capabilities.

Wally prints a `WALLY_READY port=<n>` line to stdout once its HTTP server is bound. `AgentClient` reads this line before completing the handshake and stores the port for the media proxy (§4.1). This avoids a pre-assigned port that could conflict with other processes.

## 6. Error Handling

Agent errors in the indexing and search paths are logged at `ERROR` level via the `woof.mcp_server` logger before being returned to the assistant as `{"error": "..."}` dicts (§3.5).

### Error categorization [Planned]

| Category | Example | User action |
|---|---|---|
| Transient | Network timeout, rate limit | Woof auto-retries (up to 3 times) |
| Permanent | Corrupt image file, unsupported format | Flagged to user; photo skipped |
| Configuration | Invalid credentials, missing permissions | Flagged to user; agent paused until resolved |

Permanent errors will be recorded per-photo in the activity log. Woof will not retry permanent errors — the user must resolve the root cause.

## 7. Planned subsystems

### 7.1 Background daemon [Planned]

The daemon already persists across host connections (§2); the planned extension is to run it as a launchd agent (macOS) started at login, independently of any host connection. This enables OS file watching (FSEvents) to detect changes while the host is closed, scheduled housekeeping/enrichment passes, and agent executions that outlast a host session. When a host opens, its bridge connects to the already-running daemon as usual.

### 7.2 Agent lifecycle state machine & timeout detection [Planned]

```
                 launch
    pending ──────────────► running
                              │
                 ┌────────────┼────────────┐
                 ▼            ▼            ▼
            completed      failed      timeout
                                     (no heartbeat
                                      for 5 min)
```

Woof will maintain each agent run's state in memory and persist it to `activity.json` on transitions. A periodic check (every 60 s) runs against all `running` agents; if an agent's last `notifications/progress` is older than the configured timeout (default 5 min):
1. Woof sends `notifications/cancelled` to the agent's MCP server.
2. Agent state transitions to `timeout`.
3. Woof revokes the agent's scoped storage token (if the backend supports revocation) or lets it expire.
4. The activity log entry is updated with status `timeout` and the last known progress.

### 7.3 Agent chaining [Planned]

After an agent completes successfully, Woof will evaluate whether dependent agents should be triggered:

| Completed agent | Next agent(s) | Condition |
|---|---|---|
| Ingestion | Housekeeping (thumbnails + manifest rebuild) | Always |
| Housekeeping | Enrichment | If unenriched photos exist in affected partitions |
| Change detection (dirty partition) | Housekeeping | After debounce window expires |

Chaining is configured declaratively, not hardcoded — new agent types declare their dependencies during the MCP `initialize` handshake.

### 7.4 Activity log [Planned]

Stored as JSON at `~/.ouestcharlie/activity.json`; each entry is a self-contained record:

```json
{
  "runs": [
    {
      "id": "run-20260220-143012-abc123",
      "agentType": "housekeeping",
      "agentId": "builtin-housekeeping-v1",
      "backend": "cloud-s3",
      "scope": ["2024/2024-07/"],
      "startTime": "2026-02-20T14:30:12Z",
      "endTime": "2026-02-20T14:32:45Z",
      "status": "completed",
      "summary": {
        "photosProcessed": 1023,
        "sidecarsUpdated": 5,
        "thumbnailsRebuilt": true,
        "errors": 0
      },
      "lastHeartbeat": "2026-02-20T14:32:40Z"
    }
  ]
}
```

Woof will prune entries older than the retention period (default 30 days) on startup and daily thereafter. The log is append-only during normal operation — no concurrent write conflicts.

### 7.5 Dirty partition queue [Planned]

Woof will maintain an in-memory queue of partitions marked dirty by change detection. Each entry tracks backend name, partition path, first/last change timestamps, and change count. Debounce: a partition is eligible for housekeeping when `now - lastChangeTimestamp > debounceWindow` (default 10 min); Woof evaluates the queue every 60 s. The queue will be persisted to `~/.ouestcharlie/dirty_partitions.json` so pending work survives a restart.

### 7.6 Partition health [Planned]

Woof will compute partition health indicators from manifest metadata, cached in memory and refreshed when manifests change:

| Indicator | Source | Computation |
|---|---|---|
| Last housekeeping run | Activity log | Most recent completed housekeeping run for the partition |
| Pending dirty changes | Dirty partition queue | Non-zero if the partition is in the queue |
| Missing thumbnails | Manifest | Photo count vs. thumbnail tile count mismatch |
| Enrichment coverage | Manifest | Percentage of photos with `ouestcharlie:faces` and `ouestcharlie:scene` populated |
```
