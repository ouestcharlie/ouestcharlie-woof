# Woof Low-Level Design

This document details the internal design of Woof. For requirements, see [woof_LLR.md](woof_LLR.md); for the reasons behind the design, see [woof_LLD_rationale.md](woof_LLD_rationale.md). For the gallery frontend's build, architecture, and testing conventions, see [gallery/README.md](../../gallery/README.md). For how Woof gets the agents (PyPI packages, or path sources to an `ouestcharlie` checkout for development), see [HLD.md § Code Organization](https://github.com/ouestcharlie/ouestcharlie/blob/master/design/HLD.md#code-organization).

The tools, their arguments, and the HTTP routes are documented in the code (tool docstrings in `mcp_server.py`, the URL scheme at the top of `http_server.py`). This document covers how the parts fit together and the implementation details that are not obvious from the code.

## 1. Overview

### 1.1 Structure of Woof

Woof runs on the user's device as a single persistent background daemon. The MCP host is the conversational UI shell; Woof is the security and operational boundary between it and the OuEstCharlie ecosystem. This document is organized around one foundational domain and three runtime domains:

- **§2 Process Lifecycle & Bootstrap** — how Woof comes into existence: the host launches a thin `woof-bridge` proxy, which discovers or lazily spawns the one long-lived daemon that everything else runs inside.
- **§3 Domain A — MCP Tools (Claude-facing)** — an MCP server exposing OuEstCharlie operations as tools the assistant invokes.
- **§4 Domain B — MCP App (gallery + its own server)** — an interactive gallery served as an MCP App resource (a sandboxed iframe), backed by local HTTP routes that serve media and session data.
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

### 1.2 How the runtime domains connect

Domains A and B are deliberately decoupled — their only coupling is two pieces of shared state, both created in one domain and consumed in another:

- **Session ids.** Domain A's search tools store match data in memory and return an opaque session id; Domain B's HTTP routes serve that data when the iframe requests it. Large match payloads never travel back through the MCP channel.
- **MCP App resource references.** Domain A's `browse_gallery` and `index_library` tools are registered with an `AppConfig` pointing at the gallery resource, which is what causes the host to open the iframe and push the tool result into it.

The two session managers (§4.2) are where these seams live: Domain A writes gallery sessions and Domain C's callbacks write indexing sessions, both served by Domain B. All three domains run inside the single daemon established in §2.

## 2. Process Lifecycle & Bootstrap (woof-bridge + daemon)

Woof is never launched directly by the host. The host is configured to run **`woof-bridge`**, a thin stdio↔HTTP proxy. This decouples the many short-lived per-connection stdio processes a host spawns from the single long-lived daemon that holds all state and does the work. The reasons for the split are in [woof_LLD_rationale.md § One daemon behind a stdio bridge](woof_LLD_rationale.md#decision-one-daemon-behind-a-stdio-bridge).

### 2.1 Bridge / daemon split

- The host spawns one `woof-bridge` stdio subprocess **per MCP connection** (a GUI host such as CoWork may open several concurrently).
- Each bridge relays MCP JSON-RPC **unmodified** between its stdio and the daemon's `/mcp/` endpoint — a transport-level pump between two `SessionMessage` stream pairs, with no protocol re-implementation and no Node/`mcp-remote` dependency (pure Python on the MCP SDK primitives Woof already uses). Malformed transport-layer messages are logged and dropped, since raw pumping has no protocol-level party to report them to.
- Exactly one Woof **daemon** runs: a single persistent uvicorn instance serving MCP (`/mcp`), the gallery, and media routes on one ephemeral loopback port. It outlives individual host connections. The bridge is the only stdio party.

### 2.2 Discovery & single-instance startup

Coordination state lives in two files under `platformdirs.user_config_dir("ouestcharlie")`:

- `woof-discovery.json` — `{pid, port, token}`, mode 0600, written atomically by the daemon once it is ready to serve.
- `woof-discovery.lock` — an advisory `filelock` guarding the check-then-spawn sequence.

`ensure_woof_running()` (bridge side) acquires the startup lock (bounded, 10 s), reads the discovery file, and probes it; if live, it returns immediately. Otherwise it removes any stale file and spawns a **detached** `python -m woof` (`start_new_session` on POSIX / `DETACHED_PROCESS` on Windows), capturing early stdout/stderr to `woof-spawn.log` — so an import-time crash before the daemon's own logging takes over is not lost to `DEVNULL` — then waits up to 15 s for a fresh discovery file. The lock serializes concurrent bridges so they never spawn competing daemons; a bridge that times out acquiring the lock falls back to waiting for the other's instance rather than failing.

`probe_alive()` is two-layered: a cheap pid-liveness check (`is_pid_alive`, platform-specific — a Windows process-handle query rather than `os.kill(pid, 0)`, which is unsafe there; a non-blocking zombie reap first on POSIX) **plus** an authenticated `GET /healthz`. The HTTP check catches the case where the recorded pid/port has been reused by an unrelated process.

### 2.3 Transport & authentication

The daemon binds `127.0.0.1` on an OS-assigned port and generates a 32-byte master token at startup (`generate_token`), recorded in the 0600 discovery file. Every request passes through one middleware stack (`asgi_server.build_http_asgi_app`), outermost first:

1. **CORS** (permissive). It is outermost so that a request rejected further in still carries CORS headers; otherwise the browser reports a misleading "blocked by CORS policy" error instead of the real 401/403.
2. **`HostOriginGuard`** — rejects (403) any request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>`. This mitigates DNS rebinding: a remote page cannot reach Woof through a hostname that resolves to 127.0.0.1.
3. **`BearerGuard`** — accepts two kinds of credential:
   - The **master token**, as `Authorization: Bearer <token>` or a `?token=` query parameter. The bridge uses it; it grants every route, including the control plane (`/mcp`, `/healthz`, `/keepalive`, `/shutdown`).
   - A **session id** as the second path segment of a session-scoped route (`/gallery/{session_id}/…`, `/indexing/{session_id}/…`). It is accepted only while that session exists in its manager (§4.2), and grants only that session's routes, never the control plane. The gallery iframe uses it: it never sees the master token.

   `/gallery-static/` (the compiled JS/CSS bundle) is exempt: `<script src>` and `<link href>` tags cannot attach a credential, and the bundle holds no user data (it is the same for every install).
4. **`ActivityMiddleware`** — records activity for idle shutdown (§2.4). It sits inside the guards, so only authenticated, correctly addressed traffic counts.

The bridge reads the master token from the discovery file and attaches it to every relayed request and to the streamable-HTTP client it opens against `/mcp/` (requested with the canonical trailing slash to avoid a 307 redirect off Starlette's mount).

Media requests have one more check, in the proxy itself (§4.1): the requested file must be one of the session's matches.

### 2.4 Idle shutdown & termination

The daemon self-terminates when quiet. `ActivityTracker.touch()` is called on every authenticated request and bridge keepalive; `watch_idle()` polls `idle_seconds()` and, once past the idle timeout (15 min by default, `WOOF_IDLE_TIMEOUT_SECONDS`), requests shutdown once. Each bridge posts `/keepalive` every 60 s for as long as the host keeps it open, so an active connection keeps the daemon alive even without traffic. The daemon also stops on an authenticated `POST /shutdown` (what `woof-bridge --stop` sends) or on a signal, and removes the discovery file on exit. `watch_idle` is dependency-injected (the idle *decision* is separated from the uvicorn server) so it is unit-testable without a real server or wall-clock timeouts.

### 2.5 Startup configuration (`config.py`)

`WoofConfig` is loaded at daemon startup and holds the library definitions used by every domain. It is serialized once, by `to_dict()` on `LibraryConfig`, for both `WOOF_BACKEND_CONFIG` (agent env) and MCP tool responses; the `status` key is added by the MCP server, not `LibraryConfig`, so the config stays unaware of MCP concerns.

**LanceDB index location on Windows UNC paths.** `object_store` (the Rust storage layer under Lance) is unreliable on UNC paths and mapped network drives. When a library root resolves to a UNC share on Windows, the LanceDB index is redirected to `%LOCALAPPDATA%\ouestcharlie\indexes\<library_name>` so it stays on local NTFS. `_resolve_to_unc` uses `Path.resolve()` first (as `LocalBackend._resolve()` does), then falls back to `WNetGetUniversalNameW` for mapped drives whose resolved anchor is not already `\\`. The ctypes import is lazy and gated on `sys.platform == "win32"`. `_migrate()` backfills `lancedb_index_path` for existing UNC libraries on the first daemon start after upgrade.

**Excluded tag prefixes.** `LibraryConfig.excluded_tag_prefixes` (default `["darktable"]`) lists the tag paths kept out of the index: a `|`-separated tag equal to or below a prefix, by whole levels and ignoring case (darktable's automatic `darktable|format|jpg` tags by default). It travels to the agents in `WOOF_BACKEND_CONFIG`; the sidecars are never changed by it. It is set through `register_library`'s `excluded_tag_prefixes` parameter rather than by editing `config.json`: omitted, a new library gets the default and a re-registered one keeps its value; `[]` disables the filter. Prefixes are normalized like tags (levels trimmed, empty levels and entries dropped) in `LibraryConfig.__post_init__`, so a value read from an older or hand-edited `config.json` is normalized too, and a `config.json` without the key gets the default. Woof doesn't depend on the toolkit, so `normalize_tag_prefixes` mirrors `ouestcharlie_toolkit.model.tags.normalize_path`. Indexed tags only change at the next full reindex, so when a re-registration changes the value, the tool result adds `reindexRequired: true` and a message.

## 3. Domain A — MCP Tools (Claude-facing)

Woof exposes OuEstCharlie capabilities as MCP tools. The assistant calls them in response to user requests. Most tools forward to an agent (§5); the ones below have behavior that is not visible from their signatures.

### 3.1 Non-blocking `index_library`

`index_library` is non-blocking: it starts an indexing session (§4.2), launches Whitebeard as a background `asyncio.Task` (via Domain C, §5.3), and returns immediately with `type: "indexing"` and the session id. It opens the gallery MCP App in indexing mode (progress bar + final summary pushed back to model context, §4.3).

### 3.2 Search → gallery handoff

The gallery display uses a two-step flow to avoid passing large match payloads back through the assistant as tool arguments (which would produce excessive `tool-input-partial` MCP notifications):

1. **`search_photos`** calls Wally for the first page of matches (≤ 500), stores it in a new gallery session (§4.2), and returns only the session id and counts. The MCP client never paginates: page navigation is a gallery/HTTP concern. The session keeps the Wally query arguments (without a page number), so the gallery can load further pages later (§4.3).
2. **`browse_gallery`** receives one or more session ids and returns the id of the session to display:
   - **One id:** the same session is reused.
   - **Several ids, few matches** (total ≤ one Wally page): their loaded matches are flattened into a new session, deduplicated by `contentHash` in first-seen order. It has a single page.
   - **Several ids, more matches:** a new *chained* session lists the source sessions in order. Its pages are the source sessions' pages one after the other, each loaded from Wally through its own source session. Matches are not deduplicated across sources in this case.

### 3.3 Opening the MCP App

`browse_gallery` and `index_library` are registered with `app=AppConfig(resource_uri=_GALLERY_URI)`. This causes the host to open the gallery MCP App resource and push the tool result into it via postMessage. The result's `type` field selects gallery vs. indexing mode in the frontend.

The gallery resource is static and shared by every session, so everything session-specific travels in the tool result: the session id and `serverUrls`, the daemon's origins. There are two origins for one port, `http://localhost:<port>` and `http://127.0.0.1:<port>`, because hosts disagree on which one their iframe CSP allows (Claude Desktop Chat requires `localhost`, CoWork requires `127.0.0.1`). The resource declares both in its CSP (`resource_domains`, `connect_domains`), and the frontend uses the first one that answers.

### 3.4 Surfacing errors to Claude

Agent errors in the indexing and search paths are logged at `ERROR` level via the `woof.mcp_server` logger before being returned to the assistant as `{"error": "..."}` dicts, so failures are visible in the Woof process log even when the assistant summarizes them briefly.

## 4. Domain B — MCP App (gallery + its server)

The gallery is a Svelte application (compiled to vanilla JS, bundled with Vite) served by Woof as an MCP App resource and rendered inside the host's conversation as a sandboxed iframe. This domain has three parts: the HTTP routes that back it (§4.1), the session state those routes expose (§4.2), and the frontend behavior (§4.3).

### 4.1 Local HTTP routes (the App backend)

The gallery routes are served by the daemon (§2), on the same loopback port as `/mcp`. Photo data never passes through the MCP channel.

**URL scheme.** Every session-scoped route carries its session id as the second path segment: `/gallery/{session_id}/…` (HTML, results, result pages, media) and `/indexing/{session_id}/…` (HTML, status, cancel). The session id is both the credential (§2.3) and the key of the data served. Media routes continue with `{kind}/{library}/{partition}/{file}`, where `{partition}` may contain slashes (e.g. `2024/2024-07`).

**Runtime model.** There is one ASGI app and one uvicorn server. `asgi_server.build_http_asgi_app` mounts the FastMCP app at `/mcp` and the gallery app at `/`, under one Starlette app that carries FastMCP's lifespan (so the MCP server's startup and shutdown still run), then wraps it in the middleware of §2.3. The FastMCP app is built with `http_app(path="/")`, not `path="/mcp"`: the `/mcp` mount strips that prefix, so an inner route at `/mcp` would never match. uvicorn serves a socket bound before the app starts, so the server's URLs are known when the MCP server is built and can go into tool results.

MCP tool calls and HTTP requests share one event loop on one OS thread. **Any synchronous work in a request handler or tool that runs longer than ~1 ms must be offloaded via `loop.run_in_executor`**: blocking the loop stalls HTTP responses and MCP messages together. Calls to Wally are async and awaited directly.

**Media proxy.** All media (thumbnails, previews, video) is forwarded to Wally's HTTP server. Woof has no direct access to backend storage: it is a pure proxy, which keeps the storage abstraction inside Wally. Before forwarding, the proxy checks that the session exists (404 otherwise) and that the requested file is one of its matches, including those of chained sessions (403 otherwise): a valid session id alone does not open other files. The `{library}` segment selects the Wally sidecar; `AgentClient.get_wally_connection(library_name)` returns its `(http_port, token)` on every request, so a restarted sidecar is picked up. If the sidecar is not running, Woof returns `503`. The proxy streams bodies with no read timeout and forwards `Range`/`Content-Range`, so `<video>` seeking works without buffering large files.

### 4.2 Session state layer (the seam)

Two in-memory managers hold the state the HTTP routes serve. Neither is persisted across daemon restarts, and both evict their oldest session when full. Session ids are random URL-safe strings (`secrets.token_urlsafe(16)`); since they are credentials (§2.3), they are never derived from the query. Access needs no locking: the HTTP routes share FastMCP's event loop, so the managers — shared by reference between MCP tool callbacks and HTTP handlers — are only ever touched on one OS thread.

**`GallerySessionManager`** — written by Domain A's search tools (§3.2), read by the `/gallery/{session_id}/…` routes. Capacity 100 sessions. Each session holds one Wally page of matches at a time (stored in arrival order: Wally applies the sort before results reach Woof), plus what it needs to fetch other pages: the library, the Wally query arguments and the page size. Each match is stamped with its library name, which the media proxy uses. The JSON sent to the gallery has the current matches and a `pageMap`: one `{pageSize, pageCount, totalCount}` entry per source query (a single entry, except for chained sessions), from which the gallery computes its page count (§4.3).

**`IndexingSessionManager`** — written by Domain C's background-task callbacks (§5.3), read by the `/indexing/{session_id}/…` routes. Capacity 20 sessions (indexing runs are infrequent). Each session tracks one Whitebeard run: library, partition scope, progress, message, final summary or error, and a status:

```
running ──► completed | failed
   │
   └─ cancel ──► cancelling ──► cancelled
```

The session keeps the run's `asyncio.Task`, so a cancel request can cancel it; `cancelled` is set when the task's `CancelledError` is observed.

### 4.3 Gallery frontend (Svelte app)

The gallery is built with Vite (Svelte compiled to vanilla JS). Practical how-to — dev/build/test commands, running coverage, adding a locale — lives in [gallery/README.md](../../gallery/README.md); the architecture, conventions, and behavior are below.

**Architecture — smart root, dumb children.** `App.svelte` owns all view/selection state and passes data + callback props down to the components (`MediaGrid`, `PreviewPanel`, `IndexingProgress`). There is no store tree and no `createEventDispatcher` — idiomatic Svelte 5 runes. The guiding rule (from an audit that found logic buried in components): **pure or cross-cutting logic lives in `lib/`, not inside a component** — a component renders and wires; anything testable in isolation (URL builders, MCP-session bootstrap, pagination math, formatters, host-size reporting) belongs one level down as its own module, documented at the source. When adding a view, reach for the module that already owns a concern rather than inlining a testable helper or a second host-communication strategy.

**Testing conventions.** Tests are colocated (`Foo.svelte` → `Foo.test.js`). Each concern is tested **once, at its own altitude**: `lib/` modules carry the exhaustive cases (pure, no DOM); component tests prove only *wiring* — that a module's output reaches the DOM and callbacks fire — not the arithmetic the lib test already covers, so an algorithm change breaks one place, not two. Coverage (`npm run test:coverage`, V8 provider) is a spotlight for untested branches, not a gate — there is no enforced threshold.

**Rendering & progressive media loading.** The grid (`MediaGrid`) shows AVIF thumbnail tiles clipped from Wally's tile grid: the whole tile-grid image is laid out at full size via an explicit `width` and then shifted into view with negative margins inside an `overflow: hidden` tile. Some MCP App hosts (e.g. VS Code) inject a default `img, video { max-width: 100% }` stylesheet that overrides that explicit width and breaks the clip; the tile `<img>` sets `max-width`/`max-height: none` to stay immune to such host defaults. Video tiles carry a play-badge overlay. Opening an item shows the `PreviewPanel`: the container is pre-sized from the item's `width`/`height` (no layout shift), the previously-shown full image stays visible while the next JPEG loads, a spinner appears only if loading exceeds ~300 ms, and the incoming image crossfades in on `load`. Videos render a `<video>` (user-initiated playback, no autoplay) using the cover-frame JPEG as poster and streaming from the video route; the detail panel shows codec, duration, and a warning when the browser cannot decode the source codec. Thumbnails and previews are fetched from the daemon's HTTP routes (§4.1); the iframe CSP restricts `img-src` to its loopback origins.

**Communication.**
- Host → gallery: the tool result (`ontoolresult`: mode, session id, `serverUrls`, §3.3) and the host context (theme, locale, display mode).
- Gallery → Woof: plain HTTP to the session routes (§4.1) — results, further pages, media, indexing status and cancel. The tool result can arrive from a restarted daemon, so each one refreshes the origins and session id.
- Gallery → host: `updateModelContext` and `sendMessage` at the end of an indexing run, and `requestDisplayMode` for fullscreen.
- Future: the gallery calling Woof tools through the host (for example, to refine a search from the gallery). Today it makes no tool calls.

**Two entry paths.** Inside an MCP host, the gallery is bootstrapped by the MCP App channel above (`lib/mcpSession.svelte.js`). Opened directly in a browser at `/gallery/{session_id}/html` or `/indexing/{session_id}/html` (development, or any non-MCP host), it reads the session id from the path and uses `location.origin` as the server. Both paths are set up at mount, because `connect()` may never resolve outside a real host.

**Pagination (double-layer).** Two coordinated layers:
- *Display pages* — `MediaGrid` renders 3 rows × viewport-width columns; the display page size is derived from the viewport (typically 12–20 items).
- *Server pages* — each session holds one Wally page (≤ 500 items). The total display-page count is **the sum of per-server-page ceilings** across the session's `pageMap`, not `ceil(totalCount / displayPageSize)`, because a display page never spans a server-page boundary (partial last pages round up independently). Counters show "Page X / Y" with absolute item indices across all server pages. This math is a pure module (`lib/pagination.js`); see the gallery README.
- Navigating past the last display page of the current server page requests the next server page (the session replaces its matches) before advancing; the reverse works for Previous. For a chained session, the server page is loaded through the source session it belongs to. Prev/Next controls render above and below the grid and are hidden when the total is a single display page.

**Indexing mode.** When the tool result has `type === 'indexing'`, the frontend renders `IndexingProgress` instead of the grid. It:
1. Polls the indexing status route every second, updating the progress bar and message.
2. Stops polling once `status` leaves `running`/`cancelling`.
3. On `completed`: formats the summary from in-component state, calls `mcpApp.updateModelContext({content:[{type:'text', text: summaryMarkdown}]})` so the model has the result next turn, then `mcpApp.sendMessage` to trigger a new turn.
4. On `failed`: displays the error and calls `updateModelContext` with an error message.
5. Offers a Stop control that posts to the cancel route; the resulting `cancelling`/`cancelled` status is user-initiated and produces no model turn.

When `mcpApp` is null (direct browser access), the MCP callbacks are skipped and the summary is shown inline only.

**Fullscreen & loading.** Fullscreen is toggled via `mcpApp.requestDisplayMode()` (and Escape exits it). A shimmer skeleton grid shows while `loading` is true (between MCP App connection and the first tool result).

**Theming.** The gallery uses the host's MCP App design tokens (`--color-text-*`, `--color-background-*`, `--color-border-*`, …) exclusively — no hardcoded colors. Two layers ensure correct colors everywhere:
1. **Inside the AI host**: on `connect()` and every `onhostcontextchanged`, the gallery calls `applyDocumentTheme(ctx.theme)` and `applyHostStyleVariables(ctx.styles.variables)` from the `@modelcontextprotocol/ext-apps` SDK, writing host token values as inline styles on `<html>` (which beat any stylesheet).
2. **Standalone (direct browser access or the Vite dev server)**: `src/global.css` defines the same token names on `:root` with canonical hex values and a `prefers-color-scheme: dark` block. These are no-ops once the host has applied its own inline values.

**Localization (design view).** The active locale is host-driven and in-memory: it is resolved from `HostContext.locale` (via the same `getHostContext()`/`onhostcontextchanged` channel as theme), falling back to `navigator.language` then the base locale, and applied without any reload. The `globalVariable` compile strategy is required **because the gallery is a sandboxed iframe** — the default cookie/URL strategies would navigate or reload the frame to switch language, which is not possible here. Only static UI chrome is localized; photo metadata, Wally's `querySummary`, and model-facing text (indexing summary markdown, `sendMessage` triggers) are assistant/data content and stay in English. Catalogue format, supported locales, and how to add one live in [gallery/README.md](../../gallery/README.md).

## 5. Domain C — Agent Controller (agent-facing MCP client)

Woof acts as an MCP client to agents. Each agent is an MCP server exposing its capabilities as tools. Agents always run on the same device, as child processes of the daemon.

### 5.1 Transport and lifecycle

| Agent | Transport | Lifecycle | Reason |
|---|---|---|---|
| Whitebeard | stdio | Ephemeral — spawned per tool call, exits on completion | Indexing is infrequent; no persistent server needed |
| Wally | Streamable HTTP on loopback | Persistent sidecar, one per library — started on the first call for that library, kept alive until the daemon stops | Wally's HTTP server must stay up to serve media between tool calls (§4.1) |

Both are started with Woof's own interpreter (`sys.executable -m whitebeard` / `-m wally`), so the agents must be installed in Woof's environment: they are Woof dependencies, not separate installs.

stdio needs no port management, network exposure, or auth layer; the OS process boundary provides isolation. Wally uses HTTP because one process serves two kinds of client: Woof's MCP calls and the media requests proxied for the gallery (§4.1).

`AgentClient` runs each Wally sidecar in a dedicated `asyncio` task that owns all its context managers (the `httpx2` client, `streamable_http_client`, `ClientSession`) and serves calls from an `asyncio.Queue`, one at a time. Entering and exiting these contexts in a single task avoids anyio's "exit cancel scope in a different task" errors. A lock ensures concurrent first calls start only one sidecar per library.

### 5.2 Agent launch and Wally connection

Woof passes the library configuration to every agent as the `WOOF_BACKEND_CONFIG` environment variable (JSON, §2.5).

Wally is started with a fresh random `WOOF_AGENT_TOKEN`. Its stdin is closed and its stderr merged into stdout. Once its HTTP server is bound to an OS-assigned port, Wally prints `WALLY_READY port=<n>`; Woof reads stdout until that line (30 s timeout; other lines are logged as warnings) and so needs no pre-assigned port. Woof then opens the MCP session at `http://127.0.0.1:<n>/mcp` with `Authorization: Bearer <token>`, and uses the same port and token for the media proxy (§4.1). If Wally exits before the line, or the MCP `initialize` handshake fails, the start fails and the error reaches the caller (it is not wrapped as an `AgentError`, see [AGENTS.md](../../AGENTS.md)). On Windows the child gets `CREATE_NO_WINDOW`, so a daemon with no console does not open a visible one.

Whitebeard is started through the MCP SDK's `stdio_client` and performs the `initialize` handshake over stdio, for each call.

### 5.3 Background agent tasks

`AgentClient.call_tool_background()` wraps the ephemeral call in an `asyncio.Task` and returns immediately, without blocking the event loop. It dispatches to three callbacks:
- `on_progress(progress, total, message)` — per MCP progress notification from the agent
- `on_complete(result)` — once, when the tool returns successfully
- `on_error(exc)` — if the tool raises `AgentError` or any unexpected exception; the exception is logged before the callback runs

All callbacks are plain synchronous functions invoked on the event loop (no thread-pool dispatch). `index_library` (§3.1) wires them directly to the indexing session manager (§4.2) — this is the write path for the indexing state Domain B serves.
