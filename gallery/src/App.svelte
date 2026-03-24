<script>
  import { onMount } from 'svelte';
  import { App } from '@modelcontextprotocol/ext-apps';
  import PhotoGrid from './components/PhotoGrid.svelte';
  import PreviewPanel from './components/PreviewPanel.svelte';

  let httpPort = $state(null);
  let backendName = $state(null);
  let matches = $state([]);
  let querySummary = $state('');
  let status = $state('');
  let loading = $state(true);
  let selectedIndex = $state(null);
  let mcpApp = $state(null);
  let isFullscreen = $state(false);
  let canFullscreen = $state(false);

  function applySession(session) {
    httpPort = session.httpPort ?? httpPort;
    backendName = session.backend;
    matches = session.matches ?? [];
    querySummary = session.querySummary ?? '';
    status = `${matches.length} photo${matches.length === 1 ? '' : 's'}`;
    loading = false;
  }

  onMount(() => {
    // Path 1: URL ?token= param — works in Chrome and any direct HTTP access.
    // app.connect() may hang indefinitely outside Claude Desktop so we cannot
    // rely on it throwing before this fallback would otherwise run.
    const token = new URLSearchParams(location.search).get('token');
    const port = location.port ? parseInt(location.port) : 80;
    if (token) {
      httpPort = port;
      fetch(`http://127.0.0.1:${port}/api/results/${token}`)
        .then(r => r.ok ? r.json() : Promise.reject(new Error(r.statusText)))
        .then(data => applySession(data))
        .catch(err => { if (!matches.length) status = `Error: ${err.message}`; });
    }

    // Path 2: MCP Apps channel — works in Claude Desktop via postMessage.
    // Not awaited: connect() may never resolve outside the host environment.
    try {
      const app = new App({ name: 'OuEstCharlie', version: '1.0.0' });
      mcpApp = app;
      app.ontoolresult = ({ content }) => {
        const text = (content ?? []).find(b => b.type === 'text')?.text;
        if (text) applySession(JSON.parse(text));
      };
      app.onhostcontextchanged = (ctx) => {
        canFullscreen = ctx?.availableDisplayModes?.includes('fullscreen') ?? false;
        isFullscreen = ctx?.displayMode === 'fullscreen';
      };
      app.connect().then(() => {
        const ctx = app.getHostContext();
        canFullscreen = ctx?.availableDisplayModes?.includes('fullscreen') ?? false;
        isFullscreen = ctx?.displayMode === 'fullscreen';
      }).catch(() => {});
    } catch (_) {}
  });

  /**
   * Returns tile geometry for clipping a thumbnail AVIF grid, or null if unavailable.
   */
  function thumbnailTile(match) {
    const { avifPath, thumbnailCols: cols, thumbnailTileSize: tileSize } = match;
    if (!httpPort || !avifPath || match.tileIndex == null || !cols || !tileSize) return null;
    const url = `http://127.0.0.1:${httpPort}/thumbnails/${backendName}/${avifPath}`;
    const col = match.tileIndex % cols;
    const row = Math.floor(match.tileIndex / cols);
    return { url, col, row, tileSize, cols };
  }

  async function toggleFullscreen() {
    if (!mcpApp) return;
    const targetMode = isFullscreen ? 'inline' : 'fullscreen';
    await mcpApp.requestDisplayMode({ mode: targetMode });
  }

  /**
   * Returns the direct JPEG preview URL for a photo, or null if unavailable.
   * The JPEG is generated on-demand by Wally and cached on disk.
   */
  function previewUrl(match) {
    if (!httpPort || !match.contentHash) return null;
    return `http://127.0.0.1:${httpPort}/previews/${backendName}/${match.partition}/${match.contentHash}.jpg`;
  }
</script>

<div class="app">
  <header>
    <h1>{querySummary || 'OuEstCharlie'}</h1>
    {#if canFullscreen}
      <button
        class="fullscreen-btn"
        onclick={toggleFullscreen}
        title={isFullscreen ? 'Exit full screen' : 'Full screen'}
      >
        {#if isFullscreen}
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <path d="M5.5 1H2a1 1 0 0 0-1 1v3.5h1.5V2.5H5.5V1zM1 11.5V15a1 1 0 0 0 1 1h3.5v-1.5H2.5V11.5H1zM14 1h-3.5v1.5h2.5V5.5H15V2a1 1 0 0 0-1-1zM13.5 13.5H11V15h3.5a1 1 0 0 0 1-1v-3.5H14v2.5z"/>
          </svg>
        {:else}
          <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <path d="M1 1h4.5v1.5H2.5V5.5H1V1zM10.5 1H15v4.5h-1.5V2.5H10.5V1zM1 10.5h1.5v2.5h3v1.5H1v-4zM13.5 13H10.5v1.5H15V10.5h-1.5V13z"/>
          </svg>
        {/if}
      </button>
    {/if}
  </header>

  <PhotoGrid
    {matches}
    {loading}
    {thumbnailTile}
    onSelect={(i) => (selectedIndex = i)}
  />

  <div class="status">{status}</div>

  {#if selectedIndex !== null}
    <PreviewPanel
      {matches}
      {selectedIndex}
      onNavigate={(i) => (selectedIndex = i)}
      {previewUrl}
      {thumbnailTile}
      onClose={() => (selectedIndex = null)}
    />
  {/if}
</div>

<style>
  :global(html),
  :global(body) {
    height: 100%;
    margin: 0;
    overflow: hidden;
  }

  .app {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: #111;
    color: #eee;
    font-family: system-ui, sans-serif;
  }
  
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    background: #1a1a1a;
    border-bottom: 1px solid #333;
  }

  header h1 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 600;
  }

  .fullscreen-btn {
    background: none;
    border: 1px solid #444;
    color: #aaa;
    cursor: pointer;
    padding: 0.3rem 0.4rem;
    border-radius: 4px;
    line-height: 0;
    flex-shrink: 0;
  }

  .fullscreen-btn:hover {
    background: #222;
    color: #eee;
    border-color: #666;
  }

  .status {
    padding: 0.4rem 1rem;
    font-size: 0.8rem;
    color: #888;
    background: #1a1a1a;
    border-top: 1px solid #333;
  }
</style>
