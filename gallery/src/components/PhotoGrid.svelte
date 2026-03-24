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
    {#each { length: SKELETON_COUNT } as _, i}
      <div class="tile skeleton" style="animation-delay: {(i % 4) * 0.1}s"></div>
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

<div class="grid">
  {#each pageMatches as match, i}
    {@const tile = thumbnailTile(match)}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div
      role="button"
      tabindex="0"
      class="tile"
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
</style>
