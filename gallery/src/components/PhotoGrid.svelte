<script>
  /**
   * @type {{
   *   matches: any[],
   *   thumbnailTile: (match: any) => {url: string, col: number, row: number, tileSize: number, cols: number} | null,
   *   onSelect: (index: number) => void,
   * }}
   */
  let { matches, thumbnailTile, onSelect } = $props();

  const DISPLAY_SIZE = 160; // CSS pixels for each displayed tile
</script>

<div class="grid">
  {#each matches as match, i}
    {@const tile = thumbnailTile(match)}
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div
      role="button"
      tabindex="0"
      class="tile"
      onclick={() => onSelect(i)}
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

<style>
  .grid {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 1rem;
    align-content: flex-start;
  }

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
