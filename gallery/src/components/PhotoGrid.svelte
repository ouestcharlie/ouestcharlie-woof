<script>
  /**
   * @type {{
   *   matches: any[],
   *   loading: boolean,
   *   selectedIndex: number | null,
   *   thumbnailTile: (match: any) => {url: string, col: number, row: number, cols: number} | null,
   *   totalCount: number,
   *   serverPage: number,
   *   serverPageSize: number,
   *   onFetchServerPage: ((page: number) => Promise<void>) | null,
   *   onSelect: (index: number) => void,
   *   onPageSelect: (index: number) => void,
   * }}
   */
  let {
    matches,
    loading = false,
    selectedIndex,
    thumbnailTile,
    totalCount = matches.length,
    serverPage = 0,
    serverPageSize = 500,
    onFetchServerPage = null,
    onSelect,
    onPageSelect,
  } = $props();

  const DISPLAY_SIZE = 160; // CSS pixels for each displayed tile
  const TILE_STRIDE = DISPLAY_SIZE + 4; // tile width + gap
  const ROWS = 3;
  // Min-height for the grid so the SDK can measure a meaningful intrinsic height:
  // ROWS tiles + (ROWS-1) gaps + top/bottom padding (1rem = 16px each).
  const GRID_MIN_HEIGHT = ROWS * DISPLAY_SIZE + (ROWS - 1) * 4 + 32;

  let gridWidth = $state(0);
  let columns = $derived(gridWidth > 0 ? Math.max(1, Math.floor((gridWidth + 4) / TILE_STRIDE)) : 1);
  let displayPageSize = $derived(columns * ROWS);

  // Local page within the current server page (derived from selectedIndex).
  let localPage = $derived(selectedIndex != null ? Math.floor(selectedIndex / displayPageSize) : 0);

  // Absolute page index across all server pages.
  let serverPageOffset = $derived(serverPage * serverPageSize);
  let absolutePage = $derived(Math.ceil(serverPageOffset / displayPageSize) + localPage);

  // Total display pages across all server pages (based on totalCount).
  let totalServerFullPages = $derived(Math.floor(totalCount / serverPageSize));
  let lastServerPageSize = $derived(totalCount - totalServerFullPages * serverPageSize);
  let localPagesPerServerPage = $derived(Math.ceil(serverPageSize / displayPageSize));
  let totalDisplayPages = $derived(Math.max(1, localPagesPerServerPage * totalServerFullPages + Math.ceil(lastServerPageSize / displayPageSize)));

  // Number of local display pages within the current server page's loaded matches.
  let localPageCount = $derived(Math.max(1, Math.ceil(matches.length / displayPageSize)));

  let hasMore = $derived((serverPage + 1) * serverPageSize < totalCount);

  let pageMatches = $derived(matches.slice(localPage * displayPageSize, (localPage + 1) * displayPageSize));

  async function prevPage() {
    if (localPage > 0) {
      onPageSelect((localPage - 1) * displayPageSize);
    } else if (serverPage > 0 && onFetchServerPage) {
      await onFetchServerPage(serverPage - 1);
      // After fetch, select last photo of the newly loaded server page.
      onPageSelect(matches.length - 1);
    }
  }

  async function nextPage() {
    console.log("reaching next page localPage=" + localPage + ", localPageCount=" + localPageCount + 
      ", hasMore=" + hasMore + ", absolutePage=" + absolutePage + ", totalCount=" + totalCount)
    if (localPage < localPageCount - 1) {
      onPageSelect((localPage + 1) * displayPageSize);
    } else if (hasMore && onFetchServerPage) {
      await onFetchServerPage(serverPage + 1);
      onPageSelect(0);
    }
  }
</script>

{#if loading}
  <div class="grid" bind:clientWidth={gridWidth} style="min-height: {GRID_MIN_HEIGHT}px">
    {#each { length: displayPageSize } as _, i (i)}
      <div class="tile skeleton" style="animation-delay: {(i % columns) * 0.1}s"></div>
    {/each}
  </div>
{:else}

<!-- Always rendered so height stays constant; invisible when single-page -->
<div class="nav nav-top" aria-hidden={totalDisplayPages <= 1} class:nav-hidden={totalDisplayPages <= 1}>
  <button disabled={absolutePage === 0} onclick={prevPage}>↑ Previous</button>
  <span>{absolutePage + 1} / {totalDisplayPages}</span>
  <button disabled={absolutePage === totalDisplayPages - 1} onclick={nextPage}>Next ↓</button>
</div>

<div class="grid" bind:clientWidth={gridWidth} style="min-height: {GRID_MIN_HEIGHT}px">
  {#each pageMatches as match, i (match.partition + '/' + match.contentHash) }
    {@const tile = thumbnailTile(match)}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div
      role="button"
      tabindex="0"
      class="tile"
      onclick={() => onSelect(localPage * displayPageSize + i)}
      title={match.filename}
    >
      {#if tile}
        <!--
          Scale the full AVIF grid to cols×DISPLAY_SIZE wide, then shift it so
          the target tile (col, row) appears at the top-left of the 160×160 clip.
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
    </div>
  {/each}
</div>

<!-- Always rendered so height stays constant; invisible when single-page -->
<div class="nav nav-bottom" aria-hidden={totalDisplayPages <= 1} class:nav-hidden={totalDisplayPages <= 1}>
  <button disabled={absolutePage === 0} onclick={prevPage}>↑ Previous</button>
  <span>{absolutePage + 1} / {totalDisplayPages}</span>
  <button disabled={absolutePage === totalDisplayPages - 1} onclick={nextPage}>Next ↓</button>
</div>

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
    background: var(--color-background-secondary);
    font-size: 0.85rem;
    color: var(--color-text-secondary);
  }

  .nav-top { border-bottom: var(--border-width-regular, 0.5px) solid var(--color-border-primary); }
  .nav-bottom { border-top: var(--border-width-regular, 0.5px) solid var(--color-border-primary); }

  .nav-hidden { visibility: hidden; pointer-events: none; }

  .nav button {
    background: var(--color-background-tertiary);
    color: var(--color-text-primary);
    border: var(--border-width-regular, 0.5px) solid var(--color-border-primary);
    border-radius: var(--border-radius-xs, 4px);
    padding: 0.25rem 0.75rem;
    cursor: pointer;
    font-size: 0.85rem;
    font-family: var(--font-sans, system-ui, sans-serif);
  }

  .nav button:hover:not(:disabled) { background: var(--color-background-primary); }
  .nav button:disabled { opacity: 0.35; cursor: default; }

  .tile {
    width: 160px;
    height: 160px;
    overflow: hidden;
    cursor: pointer;
    border-radius: var(--border-radius-xs, 4px);
    background: var(--color-background-secondary);
    flex-shrink: 0;
    position: relative;
  }

  @keyframes shimmer {
    0%   { background-position: -320px 0; }
    100% { background-position: 320px 0; }
  }

  .skeleton {
    background: linear-gradient(
      90deg,
      var(--color-background-secondary) 25%,
      var(--color-background-primary) 50%,
      var(--color-background-secondary) 75%
    );
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
    color: var(--color-text-tertiary);
    text-align: center;
    padding: 0.5rem;
    word-break: break-all;
  }
</style>
