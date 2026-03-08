<script>
  /**
   * @type {{
   *   match: any,
   *   previewTile: (match: any) => {url: string, col: number, row: number, tileSize: number, cols: number} | null,
   *   thumbnailTile: (match: any) => {url: string, col: number, row: number, tileSize: number, cols: number} | null,
   *   onClose: () => void,
   * }}
   */
  let { match, previewTile, thumbnailTile, onClose } = $props();

  // Use preview tile if available, fall back to thumbnail tile
  let tile = $derived(previewTile(match) ?? thumbnailTile(match));

  // Scale the tile so it fits within 85% of the smaller viewport dimension
  let displaySize = $derived(
    tile
      ? Math.min(tile.tileSize, Math.min(window.innerWidth * 0.85, window.innerHeight * 0.82))
      : 0
  );
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="overlay" onclick={onClose}>
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div class="panel" onclick={(e) => e.stopPropagation()}>
    <button class="close" onclick={onClose}>✕</button>
    {#if tile}
      <!--
        Clip to displaySize × displaySize.
        Scale the full grid proportionally so each tile = displaySize px.
      -->
      <div class="tile-clip" style="width: {displaySize}px; height: {displaySize}px;">
        <img
          src={tile.url}
          alt={match.filename}
          style="
            width: {tile.cols * displaySize}px;
            height: auto;
            margin-left: -{tile.col * displaySize}px;
            margin-top: -{tile.row * displaySize}px;
            display: block;
          "
        />
      </div>
    {/if}
    <div class="meta">
      <div class="filename">{match.filename}</div>
      {#if match.dateTaken}
        <div class="detail">{match.dateTaken}</div>
      {/if}
      {#if match.make || match.model}
        <div class="detail">{[match.make, match.model].filter(Boolean).join(' ')}</div>
      {/if}
      {#if match.tags?.length}
        <div class="detail">Tags: {match.tags.join(', ')}</div>
      {/if}
    </div>
  </div>
</div>

<style>
  .overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.85);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }

  .panel {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
  }

  .tile-clip {
    overflow: hidden;
    border-radius: 4px;
    flex-shrink: 0;
  }

  .close {
    position: absolute;
    top: -2rem;
    right: 0;
    background: #333;
    border: none;
    color: #eee;
    padding: 0.3rem 0.6rem;
    cursor: pointer;
    border-radius: 4px;
  }

  .meta {
    text-align: center;
    font-size: 0.85rem;
    color: #aaa;
  }

  .filename {
    color: #eee;
    font-weight: 500;
  }

  .detail {
    font-size: 0.75rem;
  }
</style>
