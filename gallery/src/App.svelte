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
      app.ontoolresult = ({ content }) => {
        const text = (content ?? []).find(b => b.type === 'text')?.text;
        if (text) applySession(JSON.parse(text));
      };
      app.connect().catch(() => {});
    } catch (_) {}
  });

  /**
   * Returns tile geometry for clipping a thumbnail AVIF grid, or null if unavailable.
   */
  function thumbnailTile(match) {
    const { thumbnailsPath: path, thumbnailCols: cols, thumbnailTileSize: tileSize } = match;
    if (!httpPort || !path || match.tileIndex == null || !cols || !tileSize) return null;
    const url = `http://127.0.0.1:${httpPort}/thumbnails/${backendName}/${match.partition}/thumbnails.avif`;
    const col = match.tileIndex % cols;
    const row = Math.floor(match.tileIndex / cols);
    return { url, col, row, tileSize, cols };
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
    padding: 0.75rem 1rem;
    background: #1a1a1a;
    border-bottom: 1px solid #333;
  }

  header h1 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 600;
  }

  .status {
    padding: 0.4rem 1rem;
    font-size: 0.8rem;
    color: #888;
    background: #1a1a1a;
    border-top: 1px solid #333;
  }
</style>
