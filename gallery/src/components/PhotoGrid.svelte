<script>
  /**
   * @type {{
   *   matches: any[],
   *   loading: boolean,
   *   selectedIndex: number | null,
   *   thumbnailTile: (match: any) => {url: string, col: number, row: number, tileSize: number, cols: number} | null,
   *   onSelect: (index: number) => void,
   *   onPageSelect: (index: number) => void,
   *   selectedHashes?: Set<string>,
   *   onToggleSelect?: (hash: string) => void,
   *   onShare?: () => void,
   * }}
   */
  let {
    matches,
    loading = false,
    selectedIndex,
    thumbnailTile,
    onSelect,
    onPageSelect,
    selectedHashes = new Set(),
    onToggleSelect,
    onShare,
    canShare = false,
  } = $props();

  const DISPLAY_SIZE = 160; // CSS pixels for each displayed tile
  const PAGE_SIZE = 16;
  const SKELETON_COUNT = 16;

  // Page is derived from selectedIndex so the grid always shows the page containing the selected photo.
  let page = $derived(selectedIndex != null ? Math.floor(selectedIndex / PAGE_SIZE) : 0);

  let pageCount = $derived(Math.max(1, Math.ceil(matches.length / PAGE_SIZE)));
  let pageMatches = $derived(matches.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE));

  function prevPage() { if (page > 0) onPageSelect((page - 1) * PAGE_SIZE); }
  function nextPage() { if (page < pageCount - 1) onPageSelect((page + 1) * PAGE_SIZE); }
</script>

{#if loading}
  <div class="grid">
    {#each { length: SKELETON_COUNT } as _, i (i)}
      <div class="tile skeleton" style="animation-delay: {(i % 4) * 0.1}s"></div>
    {/each}
  </div>
{:else}

<div class="toolbar">
  <button
    class="share-btn"
    title="Share to host"
    disabled={!canShare || selectedHashes.size === 0}
    onclick={() => onShare?.()}
  >
    <!-- share icon -->
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 1l4 4H9v5H7V5H4L8 1zM2 11v3h12v-3h-1.5v1.5h-9V11H2z"/>
    </svg>
    {#if selectedHashes.size > 0}
      <span class="badge">{selectedHashes.size}</span>
    {/if}
  </button>
</div>

{#if pageCount > 1}
  <div class="nav nav-top">
    <button disabled={page === 0} onclick={prevPage}>↑ Previous</button>
    <span>{page + 1} / {pageCount}</span>
    <button disabled={page === pageCount - 1} onclick={nextPage}>Next ↓</button>
  </div>
{/if}

<div class="grid">
  {#each pageMatches as match, i (match.contentHash ?? i)}
    {@const tile = thumbnailTile(match)}
    {@const hash = match.contentHash ?? ''}
    {@const isSelected = hash && selectedHashes.has(hash)}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div
      role="button"
      tabindex="0"
      class="tile"
      class:selected={isSelected}
      onclick={() => onSelect(page * PAGE_SIZE + i)}
      title={match.filename}
    >
      {#if tile}
        <!--
          Scale the full AVIF grid to cols×DISPLAY_SIZE wide, then shift it so
          the target tile (col, row) appears at the top-left of the 160×160 clip.
          Scale = DISPLAY_SIZE / tileSize, applied by setting img width to cols*DISPLAY_SIZE.
        -->
        <img
          src={tile.url}
          alt={match.filename}
          style="
            width: {tile.cols * DISPLAY_SIZE}px;
            height: auto;
            margin-left: -{tile.col * DISPLAY_SIZE}px;
            margin-top: -{tile.row * DISPLAY_SIZE}px;
            display: block;
          "
        />
      {:else}
        <span class="label">{match.filename}</span>
      {/if}
      {#if hash}
        <!-- svelte-ignore a11y_click_events_have_key_events -->
        <div
          role="checkbox"
          tabindex="0"
          aria-checked={isSelected}
          class="check"
          onclick={(e) => { e.stopPropagation(); onToggleSelect?.(hash); }}
        >
          {#if isSelected}
            <svg width="12" height="12" viewBox="0 0 12 12" fill="currentColor" aria-hidden="true">
              <path d="M2 6l3 3 5-5-1-1-4 4-2-2z"/>
            </svg>
          {/if}
        </div>
      {/if}
    </div>
  {/each}
</div>

{#if pageCount > 1}
  <div class="nav nav-bottom">
    <button disabled={page === 0} onclick={prevPage}>↑ Previous</button>
    <span>{page + 1} / {pageCount}</span>
    <button disabled={page === pageCount - 1} onclick={nextPage}>Next ↓</button>
  </div>
{/if}

{/if}

<style>
  .grid {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 1rem;
    align-content: flex-start;
    overflow-y: auto;
  }

  .nav {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 1rem;
    padding: 0.5rem 1rem;
    background: #1a1a1a;
    font-size: 0.85rem;
    color: #aaa;
  }

  .nav-top { border-bottom: 1px solid #333; }
  .nav-bottom { border-top: 1px solid #333; }

  .nav button {
    background: #2a2a2a;
    color: #ddd;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 0.25rem 0.75rem;
    cursor: pointer;
    font-size: 0.85rem;
  }

  .nav button:hover:not(:disabled) { background: #3a3a3a; }
  .nav button:disabled { opacity: 0.35; cursor: default; }

  .tile {
    width: 160px;
    height: 160px;
    overflow: hidden;
    cursor: pointer;
    border-radius: 4px;
    background: #222;
    flex-shrink: 0;
    position: relative;
  }

  @keyframes shimmer {
    0%   { background-position: -320px 0; }
    100% { background-position: 320px 0; }
  }

  .skeleton {
    background: linear-gradient(90deg, #222 25%, #2e2e2e 50%, #222 75%);
    background-size: 320px 100%;
    animation: shimmer 1.4s infinite;
    cursor: default;
  }

  .label {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.7rem;
    color: #888;
    text-align: center;
    padding: 0.5rem;
    word-break: break-all;
  }

  .toolbar {
    display: flex;
    align-items: center;
    padding: 0.4rem 1rem;
    background: #1a1a1a;
    border-bottom: 1px solid #333;
  }

  .share-btn {
    position: relative;
    display: flex;
    align-items: center;
    gap: 0.3rem;
    background: none;
    border: 1px solid #444;
    color: #aaa;
    cursor: pointer;
    padding: 0.3rem 0.45rem;
    border-radius: 4px;
    line-height: 0;
  }

  .share-btn:hover:not(:disabled) {
    background: #222;
    color: #eee;
    border-color: #666;
  }

  .share-btn:disabled {
    opacity: 0.35;
    cursor: default;
  }

  .badge {
    font-size: 0.7rem;
    line-height: 1;
    color: #eee;
    background: #555;
    border-radius: 8px;
    padding: 0 0.35rem;
    min-width: 1.1rem;
    text-align: center;
  }

  .tile.selected {
    outline: 2px solid #8af;
    outline-offset: -2px;
  }

  .check {
    position: absolute;
    top: 6px;
    left: 6px;
    width: 18px;
    height: 18px;
    border-radius: 3px;
    border: 1.5px solid rgba(255,255,255,0.7);
    background: rgba(0,0,0,0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.1s;
    z-index: 1;
    cursor: pointer;
  }

  .tile:hover .check,
  .tile.selected .check {
    opacity: 1;
  }

  .tile.selected .check {
    background: #8af;
    border-color: #8af;
    color: #000;
  }
</style>
