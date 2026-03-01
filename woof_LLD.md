# Woof Low-Level Design

This document details the internal design of Woof. For requirements, see [woof_LLR.md](woof_LLR.md). For MCP tool definitions, see [controller_api.json](../controller_api.json).

## Architecture Overview

Woof runs as a long-lived background process (launchd agent on macOS) on the user's device. It serves three roles:

1. **MCP server**: exposes OuEstCharlie operations as MCP tools to Claude Desktop
2. **MCP App host**: serves an interactive gallery HTML/JS application rendered inside Claude Desktop's conversation
3. **Agent controller**: launches and supervises agent child processes via MCP (as MCP client to agents)

Claude Desktop is the conversational UI shell. Woof is the security and operational boundary between Claude and the OuEstCharlie ecosystem.

```
Claude Desktop (MCP client)
  └── Woof MCP server (background daemon)
        ├── MCP tools: search, browse, index, enrich, status, configure ...
        ├── MCP App resource: gallery HTML/JS (iframe in Claude Desktop conversation)
        │     └── calls back to Woof MCP tools (bidirectional via postMessage)
        ├── Local HTTP server (127.0.0.1, random port): thumbnails and previews
        ├── Credential vault (OS keychain)
        ├── Configuration (~/.ouestcharlie/)
        └── Agent controller (Woof as MCP client to agents)
              ├── Whitebeard — indexing agent (MCP server, stdio child process)
              ├── Wally — consumption agent (MCP server, stdio child process)
              └── [future enrichment agents]
```

## MCP Server (Claude-facing)

Woof exposes OuEstCharlie capabilities as MCP tools to Claude Desktop. Claude calls these tools in response to user requests. Tool categories:

- **Library management**: `index_backend`, `list_backends`, `get_status`
- **Search and browse**: `search_photos`, `browse_gallery` (returns MCP App reference)
- **Album operations**: `list_albums`, `create_album`, `add_to_album`
- **Configuration**: `add_backend`, `configure_credentials`

The gallery tool (`browse_gallery`) returns a result that includes a `_meta.ui.resourceUri` pointing to the gallery MCP App resource. Claude Desktop fetches the HTML from Woof and renders it in a sandboxed iframe inside the conversation.

## Gallery MCP App

The gallery is a Svelte application (compiled to vanilla JS, bundled with Vite) served by Woof as an MCP App resource. It renders inside Claude Desktop's conversation as a sandboxed iframe.

**Thumbnail delivery**: The gallery fetches thumbnails from Woof's local HTTP server (`http://127.0.0.1:<port>/thumbnails/...`). The iframe Content Security Policy restricts `img-src` to this local origin only. Photo data never passes through Claude's MCP channel.

**Bidirectional flow**:
- Gallery → Woof: tool calls for search, navigation, album actions (via postMessage/MCP App protocol)
- Woof → Gallery: push fresh results as search completes, update indexing progress indicators

## Local HTTP Server

Woof runs a local HTTP server bound to `127.0.0.1` on a randomly assigned port, serving:
- Thumbnail AVIF containers (`/thumbnails/<backend>/<partition>/thumbnails.avif`)
- Preview AVIF containers (`/previews/<backend>/<partition>/previews.avif`)

The port is communicated to the gallery iframe via the `ui/initialize` message at iframe startup. No external network access is permitted — the server binds loopback only.

## MCP Client (Agent-facing)

Woof acts as an MCP client to agents. Each agent is an MCP server that exposes its capabilities as tools.

### Transport

- **stdio** (default): Woof launches agents as child processes and communicates over stdin/stdout. This is the simplest model — no port management, no network exposure, no authentication layer needed. The OS process boundary provides isolation.
- **Streamable HTTP**: for agents running as separate processes or containers, Woof connects via HTTP. The agent exposes an MCP endpoint on a configurable URL.

### Agent launch (stdio transport)

When Woof launches an agent as a child process, it passes backend credentials and scope as environment variables:

```
WOOF_BACKEND_CONFIG=<JSON backend connection info>
WOOF_AGENT_TOKEN=<scoped-storage-token>
```

Woof then performs the MCP `initialize` handshake over stdio, receiving the agent's tool definitions and capabilities.

## Background Daemon

Woof runs as a launchd agent (macOS) started at login, independently of Claude Desktop. This enables:
- OS file watching (FSEvents) to detect changes while Claude Desktop is closed
- Scheduled housekeeping and enrichment passes
- Agent executions that outlast a Claude Desktop session

When Claude Desktop opens, it connects to the already-running Woof instance via the MCP transport declared in the Desktop Extension manifest.

## Agent Lifecycle State Machine

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

Woof maintains the state of each agent run in memory and persists it to `activity.json` on state transitions.

### Timeout detection

Woof runs a periodic check (every 60 seconds) against all `running` agents. If an agent's last `notifications/progress` is older than the configured timeout (default: 5 minutes):

1. Woof sends `notifications/cancelled` to the agent's MCP server
2. Agent state transitions to `timeout`
3. Woof revokes the agent's scoped storage token (if the backend supports revocation) or lets it expire
4. The activity log entry is updated with status `timeout` and the last known progress

### Agent chaining

After an agent completes successfully, Woof evaluates whether dependent agents should be triggered:

| Completed agent | Next agent(s) | Condition |
|---|---|---|
| Ingestion | Housekeeping (thumbnails + manifest rebuild) | Always |
| Housekeeping | Enrichment | If unenriched photos exist in affected partitions |
| Change detection (dirty partition) | Housekeeping | After debounce window expires |

Chaining is configured declaratively, not hardcoded — new agent types can declare their dependencies during the MCP `initialize` handshake.

## Activity Log

### Storage

The activity log is stored as a JSON file at `~/.ouestcharlie/activity.json`. Each entry is a self-contained record:

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

Woof prunes entries older than the retention period (default: 30 days) on startup and daily thereafter. The log is append-only during normal operation — no concurrent write conflicts.

## Dirty Partition Queue

Woof maintains an in-memory queue of partitions marked dirty by the change detection pipeline. Each entry tracks:

- Backend name
- Partition path
- First change timestamp
- Last change timestamp
- Change count

The debounce logic: a partition is eligible for housekeeping when `now - lastChangeTimestamp > debounceWindow` (default: 10 minutes). Woof evaluates the queue every 60 seconds.

The dirty queue is persisted to `~/.ouestcharlie/dirty_partitions.json` so that pending work survives a Woof restart.

## Partition Health

Woof computes partition health indicators by reading manifest metadata. These are cached in memory and refreshed when manifests change:

| Indicator | Source | Computation |
|---|---|---|
| Last housekeeping run | Activity log | Most recent completed housekeeping run for the partition |
| Pending dirty changes | Dirty partition queue | Non-zero if partition is in the queue |
| Missing thumbnails | Manifest | Photo count vs. thumbnail tile count mismatch |
| Enrichment coverage | Manifest | Percentage of photos with `ouestcharlie:faces` and `ouestcharlie:scene` fields populated |

## Error Handling

Agent errors are categorized for the user surface:

| Category | Example | User action |
|---|---|---|
| Transient | Network timeout, rate limit | Woof auto-retries (up to 3 times) |
| Permanent | Corrupt image file, unsupported format | Flagged to user; photo skipped |
| Configuration | Invalid credentials, missing permissions | Flagged to user; agent paused until resolved |

Permanent errors are recorded per-photo in the activity log. Woof does not retry permanent errors — the user must resolve the root cause.
