<script>
  /**
   * @type {{
   *   matches: any[],
   *   loading: boolean,
   *   selectedIndex: number | null,
   *   thumbnailTile: (match: any) => {url: string, col: number, row: number, tileSize: number, cols: number} | null,
   *   onSelect: (index: number) => void,
   *   onPageSelect: (index: number) => void,
   * }}
   */
  let { matches, loading = false, selectedIndex, thumbnailTile, onSelect, onPageSelect } = $props();

  const DISPLAY_SIZE = 160; // CSS pixels for each displayed tile
  const TILE_STRIDE = DISPLAY_SIZE + 4; // tile width + gap
  const ROWS = 3;

  let gridWidth = $state(0);
  let columns = $derived(gridWidth > 0 ? Math.max(1, Math.floor((gridWidth + 4) / TILE_STRIDE)) : 1);
  let pageSize = $derived(columns * ROWS);

  // Page is derived from selectedIndex so the grid always shows the page containing the selected photo.
  let page = $derived(selectedIndex != null ? Math.floor(selectedIndex / pageSize) : 0);

  let pageCount = $derived(Math.max(1, Math.ceil(matches.length / pageSize)));
  let pageMatches = $derived(matches.slice(page * pageSize, (page + 1) * pageSize));

  function prevPage() { if (page > 0) onPageSelect((page - 1) * pageSize); }
  function nextPage() { if (page < pageCount - 1) onPageSelect((page + 1) * pageSize); }
</script>

{#if loading}
  <div class="grid" bind:clientWidth={gridWidth}>
    {#each { length: pageSize } as _, i (i)}
      <div class="tile skeleton" style="animation-delay: {(i % columns) * 0.1}s"></div>
    {/each}
  </div>
{:else}

{#if pageCount > 1}
  <div class="nav nav-top">
    <button disabled={page === 0} onclick={prevPage}>↑ Previous</button>
    <span>{page + 1} / {pageCount}</span>
    <button disabled={page === pageCount - 1} onclick={nextPage}>Next ↓</button>
  </div>
{/if}

<div class="grid" bind:clientWidth={gridWidth}>
  {#each pageMatches as match, i (match.contentHash ?? i)}
    {@const tile = thumbnailTile(match)}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <div
      role="button"
      tabindex="0"
      class="tile"
      onclick={() => onSelect(page * pageSize + i)}
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
    background: var(--color-background-secondary);
    font-size: 0.85rem;
    color: var(--color-text-secondary);
  }

  .nav-top { border-bottom: var(--border-width-regular, 0.5px) solid var(--color-border-primary); }
  .nav-bottom { border-top: var(--border-width-regular, 0.5px) solid var(--color-border-primary); }

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
