# OuEstCharlie — Woof

Woof is the central controller for OuEstCharlie. It bridges Claude Desktop with the indexing and search agents (Whitebeard, Wally) via the Model Context Protocol.

## Roles

1. **MCP server** → Claude Desktop: exposes `add_library`, `list_libraries`, `get_status`, `index_library`, `search_photos`, `browse_gallery`
2. **MCP client** → agents: launches Whitebeard and Wally as stdio child processes and calls their tools
3. **HTTP server**: serves thumbnail/preview AVIF containers on `127.0.0.1:<random port>` for the gallery iframe

## Design Documents

| Document | Purpose |
|----------|---------|
| [woof_LLR.md](doc/design/woof_LLR.md) | Low-level requirements |
| [woof_LLD.md](doc/design/woof_LLD.md) | Low-level design |
| [woof_LLD_rationale.md](doc/design/woof_LLD_rationale.md) | Design rationale and alternatives |

## Repository Structure

Server Python sources:

```
src/woof/

```

Gallery in Svelte (Javascript) sources & tests:

```
gallery/              # Svelte source (npm run build → dist/)
```

Python tests:
```
tests/
tests_integration
```

## Installation

### From PyPI (recommended)

```bash
pip install ouestcharlie-woof
```

### From source (development)

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

#### Enable pre-commit hooks (recommended)

```bash
pip install pre-commit
pre-commit install
```

Runs `ruff` (Python linter/formatter) and `eslint` (gallery JS/Svelte) automatically before each commit.

#### Rebuild the gallery (only needed when editing Svelte source)

```bash
cd gallery
npm install
npm run build
# Produces src/woof/gallery/dist/index.html (self-contained Svelte bundle)
```

## Running Tests

**Always use `.venv/bin/python -m pytest`:**

```bash
.venv/bin/python -m pytest tests/ -v
```

## Running the application

### Woof and woof-bridge

The Woof server runs in Streamable HTTP. It is launched and kept alive by `woof-bridge`. 


`woof-bridge` is a thin stdio↔HTTP proxy: it lazily starts (or reuses) one persistent Woof HTTP instance and relays MCP traffic to it, so multiple simultaneous connections from the same host (e.g. Claude CoWork) all reach the same running instance. Run `woof-bridge --stop` to stop it manually — it otherwise shuts itself down automatically after being idle for a while. 

For debugging or the MCP Inspector workflow, you can still run Woof directly in stdio mode with `WOOF_TRANSPORT=stdio uvx --python 3.13 --from ouestcharlie-woof woof`.


### With uvx (recommended — no manual install)

See [README.md](README.md) for the standard startup using `woof-bridge`.

### In Claude Config with a local venv (development)

```json
{
  "mcpServers": {
    "ouestcharlie": {
      "command": "/path/to/ouestcharlie-woof/.venv/bin/woof-bridge",
      "args": []
    }
  }
}
```

Restart Claude Desktop after editing the config. Woof is launched on demand when Claude Desktop starts.


### Check started woof



```
 cat "$HOME/Library/Application Support/ouestcharlie/woof-discovery.json"
 ```

## MCP Inspector (development)

Woof defaults to `WOOF_TRANSPORT=http` (a single persistent HTTP instance, discovered via a
discovery file — see `woof.discovery`/`woof.bridge`). The inspector drives the module-level `mcp`
object directly over stdio, so `WOOF_TRANSPORT` must be set to `stdio` *before* import for its
lifespan to start the separate gallery/media server the inspector expects.

Use `fastmcp dev inspector`, not the base `mcp` SDK's `mcp dev`:

```bash
source .venv/bin/activate
WOOF_TRANSPORT=stdio fastmcp dev inspector src/woof/__main__.py
```

Activate the venv rather than invoking `.venv/bin/fastmcp` directly — the Inspector UI it launches
re-spawns the server subprocess using a bare `fastmcp` command (`fastmcp run ...`, resolved via
`PATH`), which fails with `spawn fastmcp ENOENT` if `.venv/bin` isn't on `PATH`.

(Woof's `mcp` object is a `fastmcp` (third-party package) `FastMCP` instance, not the base SDK's own `FastMCP` type that `mcp dev` expects, so `mcp dev` fails with "not a valid server object")

## Context

| Repository | Purpose |
|------------|---------|
| [ouestcharlie](https://github.com/ouestcharlie/ouestcharlie/) | Architecture docs, HLR/HLD, MCP interface |
| [**ouestcharlie-woof** *(this repo)*](https://github.com/ouestcharlie/ouestcharlie-woof/) | Woof controller |
| [ouestcharlie-py-toolkit](https://github.com/ouestcharlie/ouestcharlie-py-toolkit) | Python toolkit for agents |
| [ouestcharlie-whitebeard](https://github.com/ouestcharlie/ouestcharlie-whitebeard) | Indexing agent |
| [ouestcharlie-wally](https://github.com/ouestcharlie/ouestcharlie-wally) | Search/consumption agent |

See [ouestcharlie/HLD.md](https://github.com/ouestcharlie/ouestcharlie/blob/master/HLD.md) for the overall system architecture.

## License

MIT license
