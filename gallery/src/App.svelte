<script>
  import { onMount, onDestroy } from 'svelte';
  import MediaGrid from './components/MediaGrid.svelte';
  import PreviewPanel from './components/PreviewPanel.svelte';
  import IndexingProgress from './components/IndexingProgress.svelte';
  import {
    initServerOrigins,
    initSessionId,
    fetchResults,
    fetchResultsPage,
    thumbnailUrl,
    previewUrl,
    videoUrl,
  } from './lib/api.svelte.js';
  import * as m from './paraglide/messages.js';
  import { applyLocale } from './lib/locale.js';
  import { notifyHostHeight } from './lib/hostSize.js';
  import { initMcpSession } from './lib/mcpSession.svelte.js';
  import { itemCountLabel } from './lib/format.js';

  let sessionId = $state(null);
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

  // body { height: 100%; overflow: hidden } prevents the SDK's autoResize (ResizeObserver on body)
  // from ever firing. Manually notify the host whenever the displayed content changes.
  //
  // Inline gallery height is sized to show 3 thumbnail rows without clipping:
  //   grid 3 rows (matches MediaGrid GRID_MIN_HEIGHT) + grid nav bars + header + status.
  const GRID_ROWS_HEIGHT = 3 * 160 + 2 * 4 + 32; // 520 — 3 tiles + gaps + 1rem padding each side
  const HEADER_STATUS = 76;                       // header bar + status bar
  const GRID_NAV = 68;                            // nav-top + nav-bottom bars
  const INLINE_GALLERY_HEIGHT = GRID_ROWS_HEIGHT + GRID_NAV + HEADER_STATUS; // 664
  // Preview has no nav bars, so its usable height is the iframe minus header+status.
  const INLINE_PREVIEW_MAX = INLINE_GALLERY_HEIGHT - HEADER_STATUS; // 588
  const INLINE_HEIGHTS = { gallery: INLINE_GALLERY_HEIGHT };
  $effect(() => {
    if (!modeKnown || !mcpApp || !mcpReady || isFullscreen) return;
    // Indexing reports its own measured height (IndexingProgress.notifyHostMeasured) —
    // a fixed value here raced it and could clamp the iframe below the real content
    // height (e.g. clipping the Stop button / summary on Windows).
    if (mode === 'indexing') return;
    notifyHostHeight(mcpApp, INLINE_HEIGHTS[mode] ?? 400);
  });

  function applySession(session, sid, page) {
    if (sid !== undefined) sessionId = sid;
    matches = session.matches ?? [];
    pageMap = session.pageMap;
    serverPage = page;
    const total = (session.pageMap ?? []).reduce((s, e) => s + e.totalCount, 0);
    status = itemCountLabel(total);
    loading = false;
    view = 'grid';
    selectedIndex = matches.length > 0 ? 0 : null;
  }

  // Load a gallery session by its session id and apply it, mapping failures to the
  // status bar. Shared by the direct-URL path and the MCP tool-result path.
  // On failure the grid stays in its loading (skeleton) state — pageMap is
  // still null, so rendering the populated grid would crash — while the error
  // surfaces in the status bar.
  async function loadGallery(sid, onError) {
    try {
      const data = await fetchResults(sid);
      applySession(data, sid, 0);
    } catch (err) {
      if (!matches.length) status = onError(err);
    }
  }

  async function fetchServerPage(page) {
    if (!sessionId) return;
    serverPageLoading = true;
    try {
      const data = await fetchResultsPage(sessionId, page);
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
    // Direct access: location.origin is the Woof server. In the MCP iframe it is
    // ui://… (unusable), but the tool result overrides origins before any request.
    initServerOrigins([location.origin]);

    // Path 1: direct HTTP access (Chrome, any non-MCP host) — works because
    // app.connect() may hang indefinitely outside Claude Desktop, so we cannot
    // rely on it throwing before this fallback would otherwise run. The session id
    // is the second path segment of /gallery/{session_id}/html or
    // /indexing/{session_id}/html — URL-safe by construction, so no decoding needed;
    // indexing metadata (library, partitions) is read from the status endpoint.
    const galleryPath = location.pathname?.match(/^\/gallery\/([^/]+)\/html$/);
    const indexingPath = location.pathname?.match(/^\/indexing\/([^/]+)\/html$/);
    if (indexingPath) {
      initSessionId(indexingPath[1]);
      indexingSessionId = indexingPath[1];
      mode = 'indexing';
      modeKnown = true;
      loading = false;
    } else if (galleryPath) {
      initSessionId(galleryPath[1]);
      modeKnown = true;
      loadGallery(galleryPath[1], err => m.status_error({ message: err.message }));
    }

    // Path 2: MCP Apps channel — works in Claude Desktop via postMessage.
    // Session bootstrap (App construction, tool-result parsing, host context,
    // connect handshake) lives in lib/mcpSession; here we only route its
    // callbacks into local view state.
    initMcpSession({
      onApp: (app) => { mcpApp = app; },
      onReady: () => { mcpReady = true; },
      onDisplayMode: ({ canFullscreen: cf, isFullscreen: fs }) => {
        if (cf !== undefined) canFullscreen = cf;
        if (fs !== undefined) isFullscreen = fs;
      },
      onIndexing: ({ sessionId }) => {
        indexingSessionId = sessionId;
        mode = 'indexing';
        modeKnown = true;
        loading = false;
      },
      onGallery: ({ querySummary: qs, sessionId: sid }) => {
        mode = 'gallery';
        modeKnown = true;
        querySummary = qs;
        loadGallery(sid, err => m.status_error_loading_gallery({ message: err.message }));
      },
    });
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
      <MediaGrid
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
          active={view === 'preview'}
          {isFullscreen}
          inlineMaxHeight={INLINE_PREVIEW_MAX}
          onNavigate={(i) => (selectedIndex = i)}
          {previewUrl}
          {videoUrl}
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
