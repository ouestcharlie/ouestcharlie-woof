# Woof — Your Photos, Your Storage, Your Rules

> **Early preview release.** Woof is functional but rough around the edges. Expect missing features, occasional errors, and breaking changes between releases. See the [status section](#status) below.

Woof is the gateway to **OuEstCharlie**, a media management system that keeps your photos exactly where they are — on your own drives — while giving you a modern, searchable gallery powered by your AI assistant.

## What makes it different

Most photo managers either lock your library into a cloud service (Google Photos, iCloud) or require a database server that becomes a single point of failure. Woof takes a different approach:

- **AI-native interface.** Woof runs as an MCP server. Your AI assistant becomes your gallery UI — browse, search, and query your photos in natural conversation.
- **No database.** Metadata is stored as XMP sidecars alongside your photos and in lightweight JSON manifests. Copy the folder, move the drive — your organization travels with your photos.
- **Open formats.** XMP is an ISO standard. JSON is universal. AVIF is royalty-free. If you ever stop using OuEstCharlie, every other tool (Lightroom, darktable, ExifTool) can still read your metadata.
- **No cloud dependency.** Your photos stay on your own storage: a local drive, a mounted cloud drive (iCloud, OneDrive), or anything accessible as a filesystem.
- **No deep AI dependency.** Only part of the metadata is shared with the AI tool (e.g. Claude Desktop), the pictures are served locally by Woof, the full metadata is managed by Woof and its companions (Wally, Whitebeard).

---

## Installation

Woof runs as a local [MCP](https://modelcontextprotocol.io/) server. It connects to your AI desktop client and exposes your photo library as a set of tools.

### Prerequisites

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) — handles Python automatically, `uvx` is included

> **Early preview**: Woof is currently published on [Test PyPI](https://test.pypi.org/project/ouestcharlie-woof/). The `--extra-index-url` flag in the configs below points there. This flag will be dropped once Woof is published on the main PyPI index.

### Connect to Claude Desktop

Add Woof to your Claude Desktop MCP configuration. Open (or create) `~/Library/Application Support/Claude/claude_desktop_config.json` and add:

```json
{
  "mcpServers": {
    "woof": {
      "command": "uvx",
      "args": ["--extra-index-url", "https://test.pypi.org/simple/", "--extra-index-url", "https://pypi.org/simple/", "--from", "ouestcharlie-woof", "woof"]
    }
  }
}
```

Restart Claude Desktop. Woof will appear as an MCP integration, and the gallery will render as an interactive panel inside your conversation.

### Connect to ChatGPT Desktop

ChatGPT Desktop supports MCP servers. Add Woof in **Settings → Connectors → Add MCP Server**:

- **Name**: Woof
- **Command**: `uvx`
- **Arguments**: `--extra-index-url https://test.pypi.org/simple/ --from ouestcharlie-woof woof`

### Connect to Goose

[Goose](https://github.com/block/goose) supports MCP servers via its extension system. Add the following to your Goose configuration (`~/.config/goose/config.yaml`):

```yaml
extensions:
  woof:
    type: stdio
    cmd: uvx
    args: ["--extra-index-url", "https://test.pypi.org/simple/", "--extra-index-url", "https://pypi.org/simple/", "--from", "ouestcharlie-woof", "woof"]
    enabled: true
```

---

## First Steps

### 1. Register a local backend

Once Woof is connected to your AI client, ask it to register your photo folder:

> *"Add a local backend to Woof pointing to /Users/yourname/Pictures"*

Woof will create a configuration entry for this folder and prepare it for indexing.

### 2. Index the backend

Trigger the indexer (Whitebeard) to scan your photos and build the metadata index:

> *"Index my local backend"*

Woof will launch the indexing agent, which will:
- Extract EXIF/XMP metadata from each photo
- Write XMP sidecar files alongside your originals
- Generate thumbnails and previews
- Build a hierarchical manifest for fast querying

Indexing a large library takes time, from 10 to 100 millisecond per picture. 

### 3. Test your first queries

Once indexing is complete, start browsing:

> *"Show me photos in Woof from last July"*

> *"In Woof, Show me pictures located close to Paris"*

> *"How many photos do I have in Woof?"*

The gallery panel will appear inline in your conversation with matching results.

---

## Status

Woof is an **early preview** targeting a focused V1 scope:

| Feature | Status |
|---|---|
| Local filesystem indexing (macOS, Linux, Windows) | Working |
| JPEG, HEIC, RAW, PNG support | Working |
| Date-based search | Working |
| Gallery view (Claude Desktop) | Working |
| Cloud backends (S3, OneDrive, iCloud Drive) | Planned for V2 |
| Enrichment agents (faces, scene recognition) | Planned for V2 |
| Albums and smart filters | Planned for V2 |
| Change detection / automatic re-indexing | Planned for V2 |
| Mobile companion app | Planned for V2 |
| Video support | Planned |

**What this means for you**: V1 works well for browsing and searching a local photo library on macOS, Linux or Windows. If you hit a bug or unexpected behavior, that's expected — please [open an issue](https://github.com/ouestcharlie/ouestcharlie-woof/issues).

## Developers' corner

For developer and architecture documentation, see [README_DEV.md](README_DEV.md).
