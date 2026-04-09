<p align="center"><img src="assets/woof_large_850.png" alt="Woof" height="360"></p>

# Woof — See your Photo Gallery in your AI assistant

> **Early preview release.** Woof is functional but rough around the edges. Expect missing features, occasional errors, and breaking changes between releases. See the [status section](#status) below.

Woof is the MCP App to **"Où Est Charlie ?"**, a media management system that keeps your photos (later movies and other media) exactly where they are — on your own drives — while giving you a beautiful, searchable gallery view powered by your AI assistant (Claude, OpenGPT, Goose...).

No cloud subscription. No proprietary lock-in. Your library, your way.

Où est Charlie?? That's "Where is Wally?" in French.

## What makes it different

Most photo managers lock your library into a cloud service (Google Photos, iCloud) or require a database server that becomes a single point of failure. Woof takes a different approach:

- **Conversation as your gallery.** Woof connects to your AI assistant (Claude Desktop, ChatGPT, Goose…) and turns it into a full photo browser. Ask in plain language, get results inline. No separate app to learn.
- **Privacy by design.** Only metadata travels to your AI assistant — your actual photos are served locally by Woof. Your pictures are never uploaded to any AI service unless you explicitly ask.
- **No database.** Metadata lives as XMP sidecar files right next to your photos, plus lightweight JSON manifests. Move a drive, copy a folder — your entire organization travels with your photos.
- **Open formats, forever.** XMP is an ISO standard. JSON is universal. AVIF is royalty-free. Every tool you already use — Lightroom, darktable, ExifTool — can read your metadata today and long after OuEstCharlie is gone.
- **Your photos are never touched.** Woof reads your library as-is. It never modifies, moves, or deletes your original files. It also honors existing XMP metadata from Lightroom, darktable, or any other tool — rather than overwriting it.
- **Works with your existing folder structure.** Just point Woof at your photos folder. No migration, no reorganization required.

---

## Installation

Woof runs as a local [MCP](https://modelcontextprotocol.io/) server. It connects to your AI desktop client and exposes your photo library as a set of tools.


### Option A — Bundle install (recommended but Claude Desktop only)

#### Connect to Claude Desktop

Download the latest `ouestcharlie-woof.mcpb` from the [Releases](https://github.com/ouestcharlie/ouestcharlie-woof/releases) page and double-click it. Claude Desktop will prompt you to install Woof in one click — no configuration file to edit.

### Option B — Manual `uvx` configuration

#### Prerequisites

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) — handles Python automatically, `uvx` is included


#### Connect to Claude Desktop

Open (or create) `~/Library/Application Support/Claude/claude_desktop_config.json` and add or update `mcpServers`:

```json
{
  "mcpServers": {
    "woof": {
      "command": "uvx",
      "args": ["--python", "3.12", "--from", "ouestcharlie-woof", "woof"]
    }
  }
}
```

Restart Claude Desktop. Woof will appear as an MCP integration, and the gallery will render as an interactive panel inside your conversation.

#### Connect to ChatGPT Desktop

ChatGPT Desktop supports MCP servers. Add Woof in **Settings → Connectors → Add MCP Server**:

- **Name**: Woof
- **Command**: `uvx`
- **Arguments**: `--python 3.12 --from ouestcharlie-woof woof`

#### Connect to Goose

[Goose](https://github.com/block/goose) supports MCP servers via its extension system. Add the following to your Goose configuration (`~/.config/goose/config.yaml`):

```yaml
extensions:
  woof:
    type: stdio
    cmd: uvx
    args: ["--python", "3.12", "--from", "ouestcharlie-woof", "woof"]
    enabled: true
```

#### Other supported AI Assistants

Other clients support MCP Apps, for example VSCode Github Copilot or Codex.

See the [MCP Extension Support Matrix](https://modelcontextprotocol.io/extensions/client-matrix)

---

## First Steps

### 1. Register your photos folder

Once Woof is connected to your AI client, ask it to register your photo folder:

> *"Add a local backend to Woof pointing to /Users/yourname/Pictures"*

Woof supports any folder on a local drive — including folders synced from iCloud Drive, OneDrive, or Google Drive, as long as the files are locally available.

### 2. Index your library

Trigger the indexer to scan your photos and build the metadata index:

> *"Index my local backend"*

Woof will launch the indexing agent, which will:
- Read EXIF/XMP metadata from each photo
- Write XMP sidecar files alongside your originals (never modifying the originals)
- Generate thumbnails and previews
- Build a fast index for querying

Indexing speed is roughly 10 to 100 seconds per 1,000 photos depending on format and hardware.

### 3. Start browsing

Once indexing is complete, just ask:

> *"Show me photos in Woof from last July"*

> *"In Woof, show me pictures taken near Paris"*

> *"How many photos do I have in Woof?"*

The gallery panel will appear inline in your conversation with matching results.

---

## Storage

V1 supports **local filesystem** backends on macOS, Linux, and Windows. This includes:
- A standard local hard drive or SSD
- A folder synced from iCloud Drive, OneDrive, Google Drive, or Infomaniak kDrive — as long as files are downloaded and locally accessible

Native cloud storage (S3, Azure, GCS, OneDrive API) is planned for V2.

---

## Status

Woof is an **early preview** targeting a focused V1 scope:

| Feature | Status |
|---|---|
| Local filesystem indexing (macOS, Linux, Windows) | Working |
| Mounted cloud drives (iCloud Drive, OneDrive, kDrive) | Working — files must be locally synced |
| JPEG, PNG, TIFF, HEIC, RAW support | Working (HEIC and RAW dependant on the build options) |
| Date, GPS bounding box, camera make and model, dimensions search | Working |
| Gallery view (Claude Desktop) | Working |
| Video support | Planned for V2 |
| Albums and smart filters | Planned for V2 |
| Share pictures with host (Claude Desktop, ChatGPT, Goose...) | Planned for V2 |
| Enrichment agents (faces, scene recognition) | Planned for V2 |
| Change detection / automatic re-indexing | Planned for V2 |
| Mobile companion app | Planned for V3 |
| Native cloud backends (S3, OneDrive, GCS…) | Planned for V3 |

**What this means for you**: V1 works well for browsing and searching a local photo library. If you hit a bug or unexpected behavior, please [open an issue](https://github.com/ouestcharlie/ouestcharlie-woof/issues).

---

## Privacy Policy

Woof is designed with privacy as a core principle.

- **Data collected**: Only photo metadata (EXIF, GPS coordinates, camera make/model, dates, file paths) is read and indexed. No account or personal information is collected.
- **Data storage**: All metadata is stored locally on your own device as XMP sidecar files and JSON manifests alongside your photos. No data is stored on any remote server.
- **AI assistant**: Only metadata and thumbnail images are sent to your AI assistant (Claude, ChatGPT, Goose…) when you perform a search. Your original photo files are never uploaded to any AI service unless you explicitly share them.
- **Third parties**: No metadata or usage data is shared with any third party.
- **Retention**: All data remains under your full control. Deleting the XMP sidecars and `.ouestcharlie/` folders from your photo library completely removes all Woof metadata.

For privacy questions, please [open an issue](https://github.com/ouestcharlie/ouestcharlie-woof/issues).

---

## Support

- **Bug reports and feature requests**: [GitHub Issues](https://github.com/ouestcharlie/ouestcharlie-woof/issues)
- **Developer documentation**: [README_DEV.md](README_DEV.md)

---

## Developers' corner

For developer and architecture documentation, see [README_DEV.md](README_DEV.md).

---

## License

MIT license
