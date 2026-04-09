# Woof Low-Level Design

This document details the internal design of Woof. For requirements, see [woof_LLR.md](woof_LLR.md). For MCP tool definitions, see [controller_api.json](https://github.com/ouestcharlie/ouestcharlie/blob/master/controller_api.json).

## Implementation status

Woof V1 implements the core search/browse/index loop. Sections marked **[Planned]** describe requirements from `woof_LLR.md` that are not yet implemented.

## Architecture Overview

Woof runs as a long-lived background process (launchd agent on macOS) on the user's device. It serves three roles:

1. **MCP server**: exposes OuEstCharlie operations as MCP tools to Claude Desktop
2. **MCP App host**: serves an interactive gallery HTML/JS application rendered inside Claude Desktop's conversation
3. **Agent controller**: launches and supervises agent child processes via MCP (as MCP client to agents)

Claude Desktop is the conversational UI shell. Woof is the security and operational boundary between Claude and the OuEstCharlie ecosystem.

```
Claude Desktop (MCP client)
  └── Woof MCP server (background daemon)
        ├── MCP tools: search, browse, index, configure ...
        ├── MCP App resource: gallery HTML/JS (iframe in Claude Desktop conversation)
        │     └── calls back to Woof MCP tools (bidirectional via postMessage)
        ├── Local HTTP server (127.0.0.1, random port): thumbnails + preview proxy
        ├── Configuration (~/.ouestcharlie/)
        └── Agent controller (Woof as MCP client to agents)
              ├── Whitebeard — indexing agent (MCP server, ephemeral stdio child process)
              ├── Wally — consumption agent (MCP server, persistent Streamable HTTP sidecar)
              │     └── HTTP server (127.0.0.1, dynamic port): serve media
              └── [future enrichment agents]
```

## MCP Server (Claude-facing)

Woof exposes OuEstCharlie capabilities as MCP tools to Claude Desktop. Claude calls these tools in response to user requests. Currently registered tools:

- **Library management**: `index_backend`
- **Search and browse**: `search_photos`, `browse_gallery` (returns MCP App reference), `get_partition_summaries`
- **Configuration**: `add_backend`, `list_backends`, `list_search_fields`

### Search → Gallery session flow

The gallery display uses a two-step flow to avoid passing large match payloads back through Claude as tool arguments (which would produce excessive `tool-input-partial` MCP notifications):

1. **`search_photos`** calls Wally, stores the full match list in an in-memory session keyed by a random `session_token`, and returns only lightweight statistics to Claude:
   ```json
   {
     "count": 41,
     "partitions": { "2024/01": 12, "2024/07": 29 },
     "date_range": { "earliest": "2024-01-03T...", "latest": "2024-07-28T..." },
     "rating_distribution": { "3": 5, "5": 2 },
     "session_token": "<22-char opaque token>"
   }
   ```
2. **`browse_gallery`** receives one or more `session_token` values, looks up the sessions, merges them (deduplicating by `contentHash`), and returns the combined match list to the gallery iframe via the MCP App tool result mechanism.

Gallery sessions are managed by `GallerySessionManager` (in-memory). Sessions are sorted by `dateTaken` ascending; photos with no `dateTaken` field sort last. When the number of sessions reaches the capacity limit (100), the oldest session is evicted. Sessions are not persisted across Woof restarts.

The `browse_gallery` tool is registered with `app=AppConfig(resource_uri=_GALLERY_URI)` which causes Claude Desktop to open the gallery MCP App resource and push the tool result into it via postMessage.

## Gallery MCP App

The gallery is a Svelte application (compiled to vanilla JS, bundled with Vite) served by Woof as an MCP App resource. It renders inside Claude Desktop's conversation as a sandboxed iframe.

**Thumbnail delivery**: The gallery fetches thumbnail tiles from Woof's local HTTP server (`http://127.0.0.1:<port>/thumbnails/...`) and preview JPEGs via the preview proxy route (`http://127.0.0.1:<port>/previews/...`). The iframe Content Security Policy restricts `img-src` to this local origin only. Photo data never passes through Claude's MCP channel.

**Progressive preview loading**: When the user opens the preview panel, the gallery immediately displays the corresponding thumbnail tile (already cached locally) scaled up and blurred as a placeholder. The full-resolution JPEG preview is fetched in the background and fades in once loaded, with no layout shift (the container is pre-sized using the photo's `width`/`height` from manifest EXIF data).

**Bidirectional flow**:
- Gallery → Woof: tool calls for search, navigation, album actions (via postMessage/MCP App protocol)
- Woof → Gallery: push fresh results as search completes, update indexing progress indicators

**Pagination**: The gallery renders 3 rows per page, with the number of columns calculated from the viewport width. Prev/Next navigation is rendered above and below the grid when `pageCount > 1`. The currently selected photo index and total count are shown in the status bar.

**Fullscreen**: The gallery supports fullscreen mode via `mcpApp.requestDisplayMode()`.

**Loading state**: A shimmer skeleton grid is shown while `loading = true` (between the MCP App connection and the first tool result arriving).

## Local HTTP Server

Woof runs a local HTTP server bound to `127.0.0.1` on a randomly assigned port, serving:

| Route | Handler |
|---|---|
| `GET /thumbnails/<backend>/<partition>/thumbnails.avif` | Proxied to Wally's HTTP server |
| `GET /previews/<backend>/<partition>/<content_hash>.jpg` | Proxied to Wally's HTTP server |
| `GET /gallery/<token>` | Gallery HTML (MCP App) |
| `GET /gallery-static/<path>` | Vite-built JS/CSS assets |
| `GET /api/results/<token>` | JSON session data (matches + metadata) |

The Woof HTTP port is communicated to the gallery iframe via the MCP App tool result. No external network access is permitted — the server binds loopback only.

### Media proxy

All media requests (`/thumbnails/...` and `/previews/...`) are forwarded to Wally's HTTP server. Woof has no direct access to backend storage — it is a pure proxy for media. This keeps the storage abstraction entirely within Wally and enables a future remote backend without any Woof changes.

Wally's HTTP port is discovered dynamically via `AgentClient.get_wally_http_port()` on every request, so port changes after a sidecar restart are picked up automatically. If Wally's port is not yet known (sidecar not started), Woof returns `503`.

## MCP Client (Agent-facing)

Woof acts as an MCP client to agents. Each agent is an MCP server that exposes its capabilities as tools.

### Transport

- **stdio** (default): Woof launches agents as child processes and communicates over stdin/stdout. This is the simplest model — no port management, no network exposure, no authentication layer needed. The OS process boundary provides isolation.
- **Streamable HTTP**: for agents running as separate processes or containers, Woof connects via HTTP. The agent exposes an MCP endpoint on a configurable URL.

### Agent lifecycle: ephemeral vs. persistent

| Agent | Lifecycle | Reason |
|---|---|---|
| Whitebeard | Ephemeral — spawned per tool call, exits on completion | Indexing is infrequent; no persistent server needed |
| Wally | Persistent sidecar — started on first search, kept alive for the Woof session | Wally's HTTP preview server must remain up to serve preview JPEGs between tool calls |

`AgentClient` manages the Wally sidecar via a dedicated `asyncio` session task backed by an `asyncio.Queue`. All tool calls to Wally are posted to this queue and executed serially inside the session task, which owns all context managers (`httpx`, `streamable_http_client`, `ClientSession`). This design avoids "exit cancel scope in different task" errors from `anyio`. The sidecar is started lazily on the first tool call for a given backend.

### Agent launch (stdio transport)

When Woof launches an agent as a child process, it passes backend credentials and scope as environment variables:

```
WOOF_BACKEND_CONFIG=<JSON backend connection info>
WOOF_AGENT_TOKEN=<scoped-storage-token>
WALLY_BACKEND_NAME=<backend name>        # Wally only — validates URL path segment
```

Woof then performs the MCP `initialize` handshake over stdio, receiving the agent's tool definitions and capabilities.

### Wally port discovery

Wally prints a `WALLY_READY port=<n>` line to stdout once its HTTP server is bound. `AgentClient` reads this line before completing the MCP handshake and stores the port for use by the media proxy. This avoids a pre-assigned port (which could conflict with other processes).

## Background Daemon

Woof runs as a launchd agent (macOS) started at login, independently of Claude Desktop. This enables:
- OS file watching (FSEvents) to detect changes while Claude Desktop is closed
- Scheduled housekeeping and enrichment passes
- Agent executions that outlast a Claude Desktop session

When Claude Desktop opens, it connects to the already-running Woof instance via the MCP transport declared in the Desktop Extension manifest.

## Agent Lifecycle State Machine [Planned]

```
                 launch
    pending ──────────────► running
                              │
                 ┌────────────┼────────────┐
                 │            │            │
                 ▼            ▼            ▼
            completed      failed      timeout
                                     (no heartbeat
                                      for 5 min)
```

Woof will maintain the state of each agent run in memory and persist it to `activity.json` on state transitions.

### Timeout detection [Planned]

Woof will run a periodic check (every 60 seconds) against all `running` agents. If an agent's last `notifications/progress` is older than the configured timeout (default: 5 minutes):

1. Woof sends `notifications/cancelled` to the agent's MCP server
2. Agent state transitions to `timeout`
3. Woof revokes the agent's scoped storage token (if the backend supports revocation) or lets it expire
4. The activity log entry is updated with status `timeout` and the last known progress

### Agent chaining [Planned]

After an agent completes successfully, Woof will evaluate whether dependent agents should be triggered:

| Completed agent | Next agent(s) | Condition |
|---|---|---|
| Ingestion | Housekeeping (thumbnails + manifest rebuild) | Always |
| Housekeeping | Enrichment | If unenriched photos exist in affected partitions |
| Change detection (dirty partition) | Housekeeping | After debounce window expires |

Chaining is configured declaratively, not hardcoded — new agent types can declare their dependencies during the MCP `initialize` handshake.

## Activity Log [Planned]

### Storage

The activity log will be stored as a JSON file at `~/.ouestcharlie/activity.json`. Each entry is a self-contained record:

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

### Pruning

Woof will prune entries older than the retention period (default: 30 days) on startup and daily thereafter. The log is append-only during normal operation — no concurrent write conflicts.

## Dirty Partition Queue [Planned]

Woof will maintain an in-memory queue of partitions marked dirty by the change detection pipeline. Each entry tracks:

- Backend name
- Partition path
- First change timestamp
- Last change timestamp
- Change count

The debounce logic: a partition is eligible for housekeeping when `now - lastChangeTimestamp > debounceWindow` (default: 10 minutes). Woof evaluates the queue every 60 seconds.

The dirty queue will be persisted to `~/.ouestcharlie/dirty_partitions.json` so that pending work survives a Woof restart.

## Partition Health [Planned]

Woof will compute partition health indicators by reading manifest metadata. These will be cached in memory and refreshed when manifests change:

| Indicator | Source | Computation |
|---|---|---|
| Last housekeeping run | Activity log | Most recent completed housekeeping run for the partition |
| Pending dirty changes | Dirty partition queue | Non-zero if partition is in the queue |
| Missing thumbnails | Manifest | Photo count vs. thumbnail tile count mismatch |
| Enrichment coverage | Manifest | Percentage of photos with `ouestcharlie:faces` and `ouestcharlie:scene` fields populated |

## Error Handling

Agent errors in `index_backend` and `search_photos` are logged at `ERROR` level via the `woof.server` logger before being returned to Claude as `{"error": "..."}` dicts. This ensures errors are visible in the Woof process log even when Claude's response summarizes them briefly.

### Error categorization [Planned]

| Category | Example | User action |
|---|---|---|
| Transient | Network timeout, rate limit | Woof auto-retries (up to 3 times) |
| Permanent | Corrupt image file, unsupported format | Flagged to user; photo skipped |
| Configuration | Invalid credentials, missing permissions | Flagged to user; agent paused until resolved |

Permanent errors will be recorded per-photo in the activity log. Woof will not retry permanent errors — the user must resolve the root cause.
