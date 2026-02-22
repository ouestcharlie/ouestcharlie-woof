# Woof LLD Rationale

This document captures the reasoning behind design decisions in [woof_LLD.md](woof_LLD.md).

## Woof Implementation Approach

### Decision: Claude Desktop + MCP Apps (selected for V1)

**Context**: Woof needs a multi-platform UI capable of displaying photo grids, spawning child processes (MCP stdio transport), and running as a long-lived controller process.

**Chosen approach**: Woof runs as a local MCP server. Claude Desktop provides the UI shell and connects to Woof as its MCP client. The gallery is served as an MCP App (interactive iframe inside Claude Desktop's conversation). See [woof_LLD.md](woof_LLD.md) for the resulting architecture.

**Why Claude Desktop wins over a standalone app**:

1. **No desktop app to build**: eliminating the Tauri/Electron layer removes a substantial development and packaging burden for V1. Woof becomes a server process; the desktop shell is provided by Anthropic.

2. **Natural language replaces a query UI**: conversational search ("show me photos from last July in Brittany") is Claude's native strength and a better user experience than filter forms for a personal photo library. No query UI to design or build.

3. **MCP alignment is already there**: agents are MCP servers. Claude Desktop is an MCP client. Woof as an MCP server is the natural bridge — the protocol is consistent end-to-end, and the architecture avoids any protocol translation layer.

4. **MCP Apps resolve the gallery problem**: since January 2026, MCP servers can render interactive HTML interfaces (iframes) inside Claude Desktop's conversation. This covers: thumbnail grid, click-to-preview, search filters, and indexing status — the full gallery surface without a standalone app. The host can push fresh data to the iframe (real-time updates). Rich media viewing is an explicitly supported use case.

5. **Thumbnail privacy is preserved**: thumbnails are served from Woof's local HTTP server (loopback only) and fetched directly by the gallery iframe. Photo pixel data never passes through Claude's API — only metadata and tool call parameters do.

6. **Multimodal bonus**: Claude can reason over images natively, describe photos, suggest album groupings, and answer natural language queries about the library — without a separate enrichment agent for descriptions.

**Known constraints accepted**:

- The gallery renders inline in the conversation, not as a persistent side panel. Opening a gallery is a chat action, not a pinned panel. This is a UX difference from a dedicated photo browser.
- User text and metadata (tool call parameters, search queries, manifest data) pass through Anthropic's infrastructure. Users subject to strict privacy requirements should be aware of this.
- Woof's UI layer depends on Anthropic's Claude Desktop product roadmap. Breaking changes in MCP Apps would require adaptation.
- Claude Desktop requires connectivity for LLM reasoning. Woof itself runs offline (indexing, change detection), but user-facing interaction requires Claude.

---

### Alternative: Standalone Desktop App (kept for reference)

If the constraints above prove unacceptable, Woof reverts to a standalone desktop application where it acts as MCP client (to agents) and UI backend simultaneously.

**Alternatives evaluated**:

| Framework | Language | Binary size | AVIF support | Process spawning | Verdict |
|---|---|---|---|---|---|
| **Tauri** | Rust + Web (JS/TS) | ~5-10 MB | System WebKit (Safari 16+, macOS Ventura+) | Rust `std::process` | Best fit if standalone app needed |
| **Electron** | JS/TS | ~150-200 MB | Chromium built-in | Node.js `child_process` | Too heavy; ecosystem advantage not worth the cost |
| **Flutter** | Dart | ~20-30 MB | Plugin (`flutter_avif`) | `dart:io Process` | Desktop still maturing |
| **Qt 6** | C++ or PySide6 | ~30-50 MB | `libavif` plugin | `QProcess` | Viable but adds a language not present elsewhere |
| **Web app (localhost)** | Any + HTML/JS | N/A | Browser-native | Backend spawns | Simplest; no system tray or native file associations |

**Tauri** would be the choice: lightweight, uses system WebKit (AVIF supported on macOS Ventura+), Rust backend handles MCP stdio naturally, web frontend (React/Svelte) for fast UI iteration, multi-platform. The web app and Tauri paths are compatible — V1 could start as a localhost web app, then wrap in Tauri (Tauri's frontend IS a web app). Zero throwaway work.

**When to reconsider**:
- Privacy requirements preclude metadata flowing through Anthropic's API
- Gallery-in-conversation UX proves insufficient for power users who need a persistent browsing surface
- Claude Desktop MCP Apps introduces breaking changes incompatible with the gallery
- Full offline operation is required including the user interaction layer

---

## References

### MCP Apps and Claude Desktop

- [MCP Apps official documentation](https://modelcontextprotocol.io/docs/extensions/apps) — iframe rendering, bidirectional communication, security model, use cases including rich media viewer and real-time monitoring
- [MCP Apps launch blog post — modelcontextprotocol.io (January 2026)](http://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/)
- [Claude supports MCP Apps — The Register (January 2026)](https://www.theregister.com/2026/01/26/claude_mcp_apps_arrives/)
- [MCP Apps guide — Bytebot](https://bytebot.io/articles/mcp-apps)
- [ext-apps repository — examples and SDK](https://github.com/modelcontextprotocol/ext-apps) — system-monitor-server (real-time updates), pdf-server and video-resource-server (media viewing), React/Vue/Svelte starter templates
- [Desktop Extensions (`.mcpb`) — Anthropic Engineering](https://www.anthropic.com/engineering/desktop-extensions)
- [MCP specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25)

### Standalone Desktop App References

- [Tauri v2](https://v2.tauri.app/)
- [Electron](https://www.electronjs.org/)
- [Flutter Desktop](https://docs.flutter.dev/platform-integration/desktop)
- [Qt 6](https://www.qt.io/product/qt6)
