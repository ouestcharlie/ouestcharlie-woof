<script>
  import { onMount, onDestroy } from 'svelte';

  /**
   * @type {{
   *   matches: any[],
   *   selectedIndex: number,
   *   onNavigate: (index: number) => void,
   *   previewUrl: (match: any) => string | null,
   *   thumbnailTile: (match: any) => {url: string, col: number, row: number, tileSize: number, cols: number} | null,
   *   onClose: () => void,
   * }}
   */
  let { matches, selectedIndex, onNavigate, previewUrl, thumbnailTile, onClose } = $props();

  let match = $derived(matches[selectedIndex]);
  let thumbTile = $derived(thumbnailTile(match));
  let jpegUrl = $derived(previewUrl(match));

  // Reset loaded state when the photo URL changes.
  let previewLoaded = $state(false);
  $effect(() => { jpegUrl; previewLoaded = false; });

  // Compute explicit pixel dimensions so the container is never 0×0.
  // (max-width + aspect-ratio alone collapses to 0 when all children are position:absolute.)
  let containerSize = $derived(
    (() => {
      const max = Math.min(window.innerWidth * 0.85, window.innerHeight * 0.82);
      const w = match?.width, h = match?.height;
      if (!w || !h) return { width: max, height: max };
      return w >= h
        ? { width: max,           height: max / (w / h) }
        : { width: max * (w / h), height: max };
    })()
  );

  let hasPrev = $derived(selectedIndex > 0);
  let hasNext = $derived(selectedIndex < matches.length - 1);

  function prev() { if (hasPrev) onNavigate(selectedIndex - 1); }
  function next() { if (hasNext) onNavigate(selectedIndex + 1); }

  function onKeydown(e) {
    if (e.key === 'ArrowLeft')  { e.preventDefault(); prev(); }
    if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
    if (e.key === 'Escape')     { onClose(); }
  }

  // Format ISO datetime string to a locale-aware human-readable form.
  // e.g. "2024-07-15T14:32:00" → "July 15, 2024 at 2:32 PM"
  function formatDate(raw) {
    if (!raw) return null;
    const d = new Date(raw);
    if (isNaN(d)) return raw;
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'long', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  // CSS background-position for the thumbnail tile used as blur placeholder.
  function thumbBgStyle(tile) {
    if (!tile) return '';
    const { url, col, row, tileSize, cols } = tile;
    const pctX = cols > 1 ? (col / (cols - 1)) * 100 : 0;
    return `background-image: url(${url}); background-size: ${cols * 100}%; background-position: ${pctX}% ${row * tileSize}px;`;
  }

  onMount(() => window.addEventListener('keydown', onKeydown));
  onDestroy(() => window.removeEventListener('keydown', onKeydown));
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="overlay" onclick={onClose}>
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div class="panel" onclick={(e) => e.stopPropagation()}>
    <button class="close" onclick={onClose}>✕</button>

    <div class="viewer">
      <button class="nav prev" onclick={prev} disabled={!hasPrev}>‹</button>

      <!--
        Container pre-sized to the photo's actual aspect ratio.
        Max dimension capped at 85vw / 82vh; the other axis follows aspect ratio.
      -->
      <div
        class="preview-container"
        style="width: {containerSize.width}px; height: {containerSize.height}px;"
      >
        <!--
          Image is always in the DOM (when jpegUrl is available) so the browser
          fetches it and fires onload reliably — display:none suppresses onload
          in some sandboxed environments (e.g. Claude Desktop iframe).
        -->
        {#if jpegUrl}
          <img
            src={jpegUrl}
            class="preview-img"
            onload={() => (previewLoaded = true)}
            alt={match.filename}
          />
        {/if}

        <!--
          Placeholder rendered on top (later in DOM = higher stacking order).
          Removed once the image has loaded, revealing the img underneath.
        -->
        {#if !previewLoaded}
          {#if thumbTile}
            <div class="thumb-placeholder" style={thumbBgStyle(thumbTile)}></div>
          {:else if jpegUrl}
            <div class="placeholder"><span>Loading…</span></div>
          {:else}
            <div class="placeholder"><span>{match.filename}</span></div>
          {/if}
        {/if}
      </div>

      <button class="nav next" onclick={next} disabled={!hasNext}>›</button>
    </div>

    <div class="meta">
      <div class="filename">{match.filename}</div>
      <div class="counter">{selectedIndex + 1} / {matches.length}</div>
      {#if match.dateTaken}
        <div class="detail">{formatDate(match.dateTaken)}</div>
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

  .viewer {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .preview-container {
    position: relative;
    overflow: hidden;
    border-radius: 4px;
    flex-shrink: 0;
    background: #222;
  }

  .thumb-placeholder {
    position: absolute;
    inset: 0;
    background-repeat: no-repeat;
    filter: blur(8px);
    transform: scale(1.05); /* hide blur edge artifacts */
  }

  .preview-img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  .placeholder {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #888;
    font-size: 0.8rem;
    background: #222;
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

  .nav {
    background: rgba(255, 255, 255, 0.1);
    border: none;
    color: #eee;
    font-size: 2rem;
    line-height: 1;
    padding: 0.4rem 0.7rem;
    cursor: pointer;
    border-radius: 4px;
    transition: background 0.15s;
    flex-shrink: 0;
  }

  .nav:hover:not(:disabled) {
    background: rgba(255, 255, 255, 0.2);
  }

  .nav:disabled {
    opacity: 0.2;
    cursor: default;
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

  .counter {
    font-size: 0.75rem;
    color: #666;
  }

  .detail {
    font-size: 0.75rem;
  }
</style>
