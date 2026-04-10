<script>
  import { onMount, onDestroy } from 'svelte';
  import { App, applyHostStyleVariables, applyDocumentTheme } from '@modelcontextprotocol/ext-apps';
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
  let view = $state('grid'); // 'grid' | 'preview'

  function applySession(session) {
    httpPort = session.httpPort ?? httpPort;
    backendName = session.backend;
    matches = session.matches ?? [];
    querySummary = session.querySummary ?? '';
    status = `${matches.length} photo${matches.length === 1 ? '' : 's'}`;
    loading = false;
    view = 'grid';
    selectedIndex = matches.length > 0 ? 0 : null;
  }

  onDestroy(() => window.removeEventListener('keydown', onKeydown));

  onMount(() => {
    window.addEventListener('keydown', onKeydown);
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
        if (ctx?.availableDisplayModes !== undefined) {
          canFullscreen = ctx.availableDisplayModes.includes('fullscreen');
        }
        if (ctx?.displayMode !== undefined) {
          isFullscreen = ctx.displayMode === 'fullscreen';
        }
        if (ctx?.theme) applyDocumentTheme(ctx.theme);
        if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
      };
      app.connect().then(() => {
        const ctx = app.getHostContext();
        canFullscreen = ctx?.availableDisplayModes?.includes('fullscreen') ?? false;
        isFullscreen = ctx?.displayMode === 'fullscreen';
        if (ctx?.theme) applyDocumentTheme(ctx.theme);
        if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
      }).catch(() => {});
    } catch { /* not running inside MCP host */ }
  });

  /**
   * Returns tile geometry for clipping a thumbnail AVIF grid, or null if unavailable.
   */
  function thumbnailTile(match) {
    const { avifPath, thumbnailCols: cols, thumbnailTileSize: tileSize } = match;
    if (!httpPort || !avifPath || match.tileIndex == null || !cols || !tileSize) return null;
    const encodedAvifPath = avifPath.split('/').map(encodeURIComponent).join('/');
    const url = `http://127.0.0.1:${httpPort}/thumbnails/${encodeURIComponent(backendName)}/${encodedAvifPath}`;
    const col = match.tileIndex % cols;
    const row = Math.floor(match.tileIndex / cols);
    return { url, col, row, tileSize, cols };
  }

  function onKeydown(e) {
    if (e.key === 'Escape' && isFullscreen) toggleFullscreen();
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
    const encodedPartition = match.partition.split('/').map(encodeURIComponent).join('/');
    return `http://127.0.0.1:${httpPort}/previews/${encodeURIComponent(backendName)}/${encodedPartition}/${encodeURIComponent(match.contentHash)}.jpg`;
  }
</script>

<div class="app">
  <header>
    <h1>{querySummary || 'OuEstCharlie'}</h1>
    <div class="header-actions">
      {#if view === 'preview' || selectedIndex !== null}
        <button
          class="view-btn"
          onclick={() => { view = view === 'grid' ? 'preview' : 'grid'; }}
          title={view === 'grid' ? 'Show preview' : 'Back to grid'}
        >
          {#if view === 'grid'}
            <!-- carousel icon -->
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M2 4h12v8H2V4zm-2 1v6h1V5H0zm15 0v6h1V5h-1zM3 5h10v6H3V5z"/>
            </svg>
          {:else}
            <!-- grid icon -->
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M1 1h6v6H1V1zm8 0h6v6H9V1zM1 9h6v6H1V9zm8 0h6v6H9V9z"/>
            </svg>
          {/if}
        </button>
      {/if}
      {#if canFullscreen && !isFullscreen}
        <button
          class="view-btn"
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
    </div>
  </header>

  <div class="view" class:hidden={view !== 'grid'}>
    <PhotoGrid
      {matches}
      {loading}
      {selectedIndex}
      {thumbnailTile}
      onSelect={(i) => { selectedIndex = i; view = 'preview'; }}
      onPageSelect={(i) => { selectedIndex = i; }}
    />
  </div>

  {#if selectedIndex !== null}
    <div class="view" class:hidden={view !== 'preview'}>
      <PreviewPanel
        {matches}
        {selectedIndex}
        onNavigate={(i) => (selectedIndex = i)}
        {previewUrl}
        {thumbnailTile}
      />
    </div>
  {/if}

  <div class="status">
    {#if view === 'preview' && selectedIndex !== null}
      {selectedIndex + 1} / {matches.length}
    {:else}
      {status}
    {/if}
  </div>
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
    overflow: hidden;
    background: var(--color-background-tertiary);
    color: var(--color-text-primary);
    font-family: var(--font-sans, system-ui, sans-serif);
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    background: var(--color-background-secondary);
    border-bottom: var(--border-width-regular, 0.5px) solid var(--color-border-primary);
  }

  header h1 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: var(--font-weight-semibold, 600);
  }

  .header-actions {
    display: flex;
    gap: 0.4rem;
    flex-shrink: 0;
  }

  .view-btn {
    background: none;
    border: var(--border-width-regular, 0.5px) solid var(--color-border-primary);
    color: var(--color-text-secondary);
    cursor: pointer;
    padding: 0.3rem 0.4rem;
    border-radius: var(--border-radius-xs, 4px);
    line-height: 0;
  }

  .view-btn:hover {
    background: var(--color-background-primary);
    color: var(--color-text-primary);
  }

  .view {
    display: contents;
  }

  .view.hidden {
    display: none;
  }

  .status {
    padding: 0.4rem 1rem;
    font-size: 0.8rem;
    color: var(--color-text-tertiary);
    background: var(--color-background-secondary);
    border-top: var(--border-width-regular, 0.5px) solid var(--color-border-primary);
  }
</style>
