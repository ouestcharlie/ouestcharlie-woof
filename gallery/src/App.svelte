<script>
  import { onMount } from 'svelte';
  import { App } from '@modelcontextprotocol/ext-apps';
  import PhotoGrid from './components/PhotoGrid.svelte';
  import PreviewPanel from './components/PreviewPanel.svelte';

  let httpPort = $state(null);
  let backendName = $state(null);
  let matches = $state([]);
  let querySummary = $state('');
  let status = $state('Loading\u2026');
  let selectedIndex = $state(null);

  function applySession(session) {
    httpPort = session.httpPort ?? httpPort;
    backendName = session.backend;
    matches = session.matches ?? [];
    querySummary = session.querySummary ?? '';
    status = `${matches.length} photo${matches.length === 1 ? '' : 's'}`;
  }

  onMount(async () => {
    // Primary: MCP Apps channel — host pushes tool result via postMessage
    try {
      const app = new App({ name: 'OuEstCharlie', version: '1.0.0' });
      app.ontoolresult = ({ content }) => {
        const text = (content ?? []).find(b => b.type === 'text')?.text;
        if (text) applySession(JSON.parse(text));
      };
      await app.connect();
    } catch (_) {
      // Fallback: direct HTTP access (dev / debug) — fetch session by URL token
      const token = location.pathname.split('/').pop();
      const port = location.port ? parseInt(location.port) : 80;
      httpPort = port;
      try {
        const response = await fetch(`http://127.0.0.1:${port}/api/results/${token}`);
        if (!response.ok) {
          const err = await response.json().catch(() => ({ error: response.statusText }));
          status = `Error: ${err.error || response.statusText}`;
          return;
        }
        applySession(await response.json());
      } catch (err) {
        status = `Error: ${err.message}`;
      }
    }
  });

  /**
   * Returns tile geometry for clipping an AVIF grid, or null if unavailable.
   * @param {'thumbnail'|'preview'} kind
   */
  function tileGeometry(match, kind) {
    const path = kind === 'thumbnail' ? match.thumbnailsPath : match.previewsPath;
    const cols = kind === 'thumbnail' ? match.thumbnailCols : match.previewCols;
    const tileSize = kind === 'thumbnail' ? match.thumbnailTileSize : match.previewTileSize;
    if (!httpPort || !path || match.tileIndex == null || !cols || !tileSize) return null;
    const avifKind = kind === 'thumbnail' ? 'thumbnails' : 'previews';
    const avifFile = kind === 'thumbnail' ? 'thumbnails.avif' : 'previews.avif';
    const url = `http://127.0.0.1:${httpPort}/${avifKind}/${backendName}/${match.partition}/${avifFile}`;
    const col = match.tileIndex % cols;
    const row = Math.floor(match.tileIndex / cols);
    return { url, col, row, tileSize, cols };
  }

  function thumbnailTile(match) { return tileGeometry(match, 'thumbnail'); }
  function previewTile(match) { return tileGeometry(match, 'preview'); }
</script>

<div class="app">
  <header>
    <h1>{querySummary || 'OuEstCharlie'}</h1>
  </header>

  <PhotoGrid
    {matches}
    {thumbnailTile}
    onSelect={(i) => (selectedIndex = i)}
  />

  <div class="status">{status}</div>

  {#if selectedIndex !== null}
    <PreviewPanel
      {matches}
      {selectedIndex}
      onNavigate={(i) => (selectedIndex = i)}
      {previewTile}
      {thumbnailTile}
      onClose={() => (selectedIndex = null)}
    />
  {/if}
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
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
