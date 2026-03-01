# OuEstCharlie — Woof

Woof is the central controller for OuEstCharlie. It bridges Claude Desktop with the indexing and search agents (Whitebeard, Wally) via the Model Context Protocol.

## Roles

1. **MCP server** → Claude Desktop: exposes `add_backend`, `list_backends`, `get_status`, `index_backend`, `search_photos`, `browse_gallery`
2. **MCP client** → agents: launches Whitebeard and Wally as stdio child processes and calls their tools
3. **HTTP server**: serves thumbnail/preview AVIF containers on `127.0.0.1:<random port>` for the gallery iframe

## Design Documents

| Document | Purpose |
|----------|---------|
| [woof_LLR.md](woof_LLR.md) | Low-level requirements |
| [woof_LLD.md](woof_LLD.md) | Low-level design |
| [woof_LLD_rationale.md](woof_LLD_rationale.md) | Design rationale and alternatives |

## Repository Structure

```
src/woof/
├── __main__.py       # Entry point (stdio MCP server)
├── server.py         # WoofServer — FastMCP tool registration
├── agent_client.py   # AgentClient — MCP client to Whitebeard / Wally
├── http_server.py    # Thumbnail HTTP server (stdlib, daemon thread)
├── config.py         # WoofConfig — ~/.ouestcharlie/config.json
└── gallery/dist/     # Pre-built Svelte gallery bundle

gallery/              # Svelte source (npm run build → dist/)
  src/
    App.svelte
    components/
      SearchForm.svelte
      PhotoGrid.svelte
      PreviewPanel.svelte
    lib/bridge.js     # MCP App postMessage bridge

tests/
  test_config.py
  test_http_server.py
  test_server.py
```

## Setup

Requires sibling repositories:

```
../ouestcharlie-py-toolkit/
../ouestcharlie-whitebeard/
../ouestcharlie-wally/
```

```bash
cd ouestcharlie-woof
uv venv
uv sync
```

### Gallery (optional — a functional placeholder is built in)

```bash
cd gallery
npm install
npm run build
# Produces src/woof/gallery/dist/index.html (self-contained Svelte bundle)
```

## Running Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

## Claude Desktop Integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ouestcharlie": {
      "command": "/path/to/ouestcharlie-woof/.venv/bin/python",
      "args": ["-m", "woof"]
    }
  }
}
```

Restart Claude Desktop. Woof is launched on demand when Claude Desktop starts.

### First use

```
add_backend name="My Photos" path="/Users/you/Pictures"
index_backend backend_name="My Photos"
search_photos backend_name="My Photos" date_min="2024-07"
browse_gallery backend_name="My Photos"
```

## MCP Inspector (development)

```bash
mcp dev src/woof/__main__.py
```

## Context

| Repository | Purpose |
|------------|---------|
| [ouestcharlie](../ouestcharlie) | Architecture docs, HLR/HLD, MCP interface |
| **ouestcharlie-woof** *(this repo)* | Woof controller |
| [ouestcharlie-py-toolkit](../ouestcharlie-py-toolkit) | Python toolkit for agents |
| [ouestcharlie-whitebeard](../ouestcharlie-whitebeard) | Indexing agent |
| [ouestcharlie-wally](../ouestcharlie-wally) | Search/consumption agent |

See [ouestcharlie/HLD.md](../ouestcharlie/HLD.md) for the overall system architecture.
