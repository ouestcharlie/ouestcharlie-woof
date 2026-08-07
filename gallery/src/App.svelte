<script>
  import { onMount, onDestroy } from 'svelte';
  import { App, applyHostStyleVariables, applyDocumentTheme } from '@modelcontextprotocol/ext-apps';
  import PhotoGrid from './components/PhotoGrid.svelte';
  import PreviewPanel from './components/PreviewPanel.svelte';
  import IndexingProgress from './components/IndexingProgress.svelte';
  import {
    initServerOrigins,
    initServerToken,
    fetchResults,
    fetchResultsPage,
    thumbnailUrl,
    previewUrl,
  } from './lib/api.svelte.js';
  import * as m from './paraglide/messages.js';
  import { applyLocale } from './lib/locale.js';

  // Photo count as a string, pluralized in the active locale.
  function photoCountLabel(n) {
    return n === 1 ? m.status_photos_one({ count: n }) : m.status_photos_other({ count: n });
  }

  function embeddedServerUrls() {
    const raw = document.documentElement.dataset.serverUrls;
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function embeddedServerToken() {
    const raw = document.documentElement.dataset.serverToken;
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  let token = $state(null);
  let matches = $state([]);
  let querySummary = $state('');
  let serverPage = $state(0);
  let pageMap = $state(null);
  let status = $state('');

  let grandTotalCount = $derived(pageMap ? pageMap.reduce((s, e) => s + e.totalCount, 0) : 0);
  let loading = $state(true);
  let serverPageLoading = $state(false);
  let selectedIndex = $state(null);
  let mcpApp = $state(null);
  let mcpReady = $state(false); // true once app.connect() resolves
  let isFullscreen = $state(false);
  let canFullscreen = $state(false);
  let view = $state('grid'); // 'grid' | 'preview'
  let mode = $state('gallery'); // 'gallery' | 'indexing'
  let modeKnown = $state(false); // false until first ontoolresult or URL param processed
  let indexingSessionId = $state(null);
  let indexingLibrary = $state('');
  let indexingPartitionScope = $state([]);

  // body { height: 100%; overflow: hidden } prevents the SDK's autoResize (ResizeObserver on body)
  // from ever firing. Manually notify the host whenever the displayed content changes.
  const INLINE_HEIGHTS = { gallery: 600, indexing: 280 };
  $effect(() => {
    if (!modeKnown || !mcpApp || !mcpReady || isFullscreen) return;
    const h = INLINE_HEIGHTS[mode] ?? 400;
    mcpApp.sendSizeChanged({ height: h }).catch(() => {});
  });

  function applySession(session, tok, page) {
    if (tok !== undefined) token = tok;
    matches = session.matches ?? [];
    pageMap = session.pageMap;
    serverPage = page;
    const total = (session.pageMap ?? []).reduce((s, e) => s + e.totalCount, 0);
    status = photoCountLabel(total);
    loading = false;
    view = 'grid';
    selectedIndex = matches.length > 0 ? 0 : null;
  }

  async function fetchServerPage(page) {
    if (!token) return;
    serverPageLoading = true;
    try {
      const data = await fetchResultsPage(token, page);
      matches = data.matches ?? [];
      serverPage = page;
    } catch (err) {
      status = m.status_error_loading_page({ message: err.message });
    } finally {
      serverPageLoading = false;
    }
  }

  onDestroy(() => window.removeEventListener('keydown', onKeydown));

  onMount(() => {
    window.addEventListener('keydown', onKeydown);
    // Initial locale from the browser; refined from the MCP host context below.
    applyLocale(navigator.language);
    initServerOrigins(embeddedServerUrls() ?? [location.origin]);
    initServerToken(embeddedServerToken());

    // Path 1: URL params — works in Chrome and any direct HTTP access.
    // app.connect() may hang indefinitely outside Claude Desktop so we cannot
    // rely on it throwing before this fallback would otherwise run.
    const urlParams = new URLSearchParams(location.search);
    const urlToken = urlParams.get('token');
    const urlSessionId = urlParams.get('sessionId');
    if (urlSessionId) {
      indexingSessionId = urlSessionId;
      indexingLibrary = urlParams.get('library') ?? '';
      const urlPartitionScope = urlParams.get('partitionScope');
      indexingPartitionScope = urlPartitionScope ? urlPartitionScope.split(',').filter(Boolean) : [];
      mode = 'indexing';
      modeKnown = true;
      loading = false;
    } else if (urlToken) {
      modeKnown = true;
      fetchResults(urlToken)
        .then(data => applySession(data, urlToken, 0))
        .catch(err => { if (!matches.length) status = m.status_error({ message: err.message }); });
    }

    // Path 2: MCP Apps channel — works in Claude Desktop via postMessage.
    // Not awaited: connect() may never resolve outside the host environment.
    try {
      const app = new App({ name: 'OuEstCharlie', version: '1.0.0' });
      mcpApp = app;
      app.ontoolresult = async ({ content }) => {
        const text = (content ?? []).find(b => b.type === 'text')?.text;
        if (!text) return;
        const result = JSON.parse(text);
        // Refresh candidate origins from the tool result — in the MCP iframe
        // context location.origin is ui://… not the Woof HTTP server URL, and
        // the server may have restarted on a new port since the page loaded.
        initServerOrigins(result.serverUrls ?? [result.serverUrl]);
        initServerToken(result.serverToken);

        if (result.type === 'indexing') {
          indexingSessionId = result.session_id;
          indexingLibrary = result.library_name;
          indexingPartitionScope = result.partition_scope ?? [];
          mode = 'indexing';
          modeKnown = true;
          loading = false;
          return;
        }

        // Gallery mode (result.type === 'gallery' or legacy without type field)
        mode = 'gallery';
        modeKnown = true;
        querySummary = result.querySummary;
        try {
          const data = await fetchResults(result.token);
          applySession(data, result.token, 0);
        } catch (err) {
          if (!matches.length) status = m.status_error_loading_gallery({ message: err.message });
          loading = false;
        }
      };
      app.onhostcontextchanged = (ctx) => {
        if (ctx?.availableDisplayModes !== undefined) {
          canFullscreen = ctx.availableDisplayModes.includes('fullscreen');
        }
        if (ctx?.displayMode !== undefined) {
          isFullscreen = ctx.displayMode === 'fullscreen';
        }
        if (ctx?.locale) applyLocale(ctx.locale);
        if (ctx?.theme) applyDocumentTheme(ctx.theme);
        if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
      };
      app.connect().then(() => {
        mcpReady = true;
        const ctx = app.getHostContext();
        canFullscreen = ctx?.availableDisplayModes?.includes('fullscreen') ?? false;
        isFullscreen = ctx?.displayMode === 'fullscreen';
        applyLocale(ctx?.locale ?? navigator.language);
        if (ctx?.theme) applyDocumentTheme(ctx.theme);
        if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
      }).catch(() => {});
    } catch { /* not running inside MCP host */ }
  });

  const AVIF_GRID_COLS = 8;

  /**
   * Returns tile geometry for clipping a thumbnail AVIF grid, or null if unavailable.
   */
  function thumbnailTile(match) {
    const url = thumbnailUrl(match);
    if (!url) return null;
    const col = match.tileIndex % AVIF_GRID_COLS;
    const row = Math.floor(match.tileIndex / AVIF_GRID_COLS);
    return { url, col, row, cols: AVIF_GRID_COLS };
  }

  function onKeydown(e) {
    if (e.key === 'Escape' && isFullscreen) toggleFullscreen();
  }

  async function toggleFullscreen() {
    if (!mcpApp) return;
    const targetMode = isFullscreen ? 'inline' : 'fullscreen';
    await mcpApp.requestDisplayMode({ mode: targetMode });
  }

</script>

<div class="app">
  {#if !modeKnown}
    <!-- waiting for first tool result — render nothing to avoid gallery skeleton flash -->
  {:else if mode === 'indexing'}
    <IndexingProgress
      sessionId={indexingSessionId}
      library={indexingLibrary}
      partitionScope={indexingPartitionScope}
      {mcpApp}
      {mcpReady}
    />
  {:else}
    <header>
      <h1>{querySummary || 'OuEstCharlie'}</h1>
      <div class="header-actions">
        {#if view === 'preview' || selectedIndex !== null}
          <button
            class="view-btn"
            onclick={() => { view = view === 'grid' ? 'preview' : 'grid'; }}
            title={view === 'grid' ? m.nav_show_preview() : m.nav_back_to_grid()}
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
            title={isFullscreen ? m.nav_exit_fullscreen() : m.nav_fullscreen()}
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
        loading={loading || serverPageLoading}
        {selectedIndex}
        {thumbnailTile}
        serverPage={serverPage}
        {pageMap}
        onFetchServerPage={fetchServerPage}
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
        />
      </div>
    {/if}

    <div class="status">
      {#if view === 'preview' && selectedIndex !== null}
        {selectedIndex + 1} / {grandTotalCount}
      {:else}
        {status}
      {/if}
    </div>
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
