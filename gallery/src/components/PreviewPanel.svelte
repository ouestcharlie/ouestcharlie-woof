<script>
  import { onMount, onDestroy } from 'svelte';

  /**
   * @type {{
   *   matches: any[],
   *   selectedIndex: number,
   *   onNavigate: (index: number) => void,
   *   previewUrl: (match: any) => string | null,
   *   thumbnailTile: (match: any) => {url: string, col: number, row: number, tileSize: number, cols: number} | null,
   * }}
   */
  let { matches, selectedIndex, onNavigate, previewUrl, thumbnailTile } = $props();

  let match = $derived(matches[selectedIndex]);
  let thumbTile = $derived(thumbnailTile(match));
  let jpegUrl = $derived(previewUrl(match));

  // Reset loaded state when the photo URL changes.
  let previewLoaded = $state(false);
  $effect(() => { jpegUrl; previewLoaded = false; });

  // Track viewport size reactively so containerSize updates on resize and fullscreen entry.
  let windowW = $state(window.innerWidth);
  let windowH = $state(window.innerHeight);

  // Compute explicit pixel dimensions so the container is never 0×0.
  // (max-width + aspect-ratio alone collapses to 0 when all children are position:absolute.)
  // CHROME_RESERVED accounts for all vertical chrome outside the image:
  //   header (~50px) + status bar (~30px) + meta block + gap (~75px) + Claude prompt overlay (~105px)
  const CHROME_RESERVED = 260;
  let containerSize = $derived(
    (() => {
      const maxH = Math.max(100, windowH - CHROME_RESERVED);
      const maxW = windowW;
      const w = match?.width, h = match?.height;
      if (!w || !h) return { width: Math.min(maxH, maxW), height: Math.min(maxH, maxW) };
      let height = maxH;
      let width = height * (w / h);
      if (width > maxW) { width = maxW; height = width / (w / h); }
      return { width, height };
    })()
  );

  let hasPrev = $derived(selectedIndex > 0);
  let hasNext = $derived(selectedIndex < matches.length - 1);

  function prev() { if (hasPrev) onNavigate(selectedIndex - 1); }
  function next() { if (hasNext) onNavigate(selectedIndex + 1); }

  function onKeydown(e) {
    if (e.key === 'ArrowLeft')  { e.preventDefault(); prev(); }
    if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
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

  function onResize() { windowW = window.innerWidth; windowH = window.innerHeight; }

  onMount(() => {
    window.addEventListener('keydown', onKeydown);
    window.addEventListener('resize', onResize);
  });
  onDestroy(() => {
    window.removeEventListener('keydown', onKeydown);
    window.removeEventListener('resize', onResize);
  });
</script>

<div class="panel">
  <div class="viewer">
    <!--
      Container pre-sized to the photo's actual aspect ratio.
      Height capped at 90vh; width follows aspect ratio, capped at 100vw.
      Nav buttons are overlaid on the image edges.
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

      <button class="nav prev" onclick={prev} disabled={!hasPrev}>‹</button>
      <button class="nav next" onclick={next} disabled={!hasNext}>›</button>
    </div>
  </div>

  <div class="meta">
    <div class="filename">{match.filename}</div>
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

<style>
  .panel {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
  }

  .viewer {
    display: flex;
  }

  .preview-container {
    position: relative;
    overflow: hidden;
    border-radius: var(--border-radius-xs, 4px);
    flex-shrink: 0;
    background: var(--color-background-secondary);
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
    color: var(--color-text-tertiary);
    font-size: 0.8rem;
    background: var(--color-background-secondary);
  }

  /* Nav arrows overlay the image — keep semi-transparent black regardless of theme */
  .nav {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 3rem;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.25);
    border: none;
    color: #fff;
    font-size: 2rem;
    line-height: 1;
    cursor: pointer;
    transition: background 0.15s;
    z-index: 1;
  }

  .nav.prev { left: 0; border-radius: var(--border-radius-xs, 4px) 0 0 var(--border-radius-xs, 4px); }
  .nav.next { right: 0; border-radius: 0 var(--border-radius-xs, 4px) var(--border-radius-xs, 4px) 0; }

  .nav:hover:not(:disabled) {
    background: rgba(0, 0, 0, 0.45);
  }

  .nav:disabled {
    opacity: 0.15;
    cursor: default;
  }

  .meta {
    text-align: center;
    font-size: 0.85rem;
    color: var(--color-text-secondary);
  }

  .filename {
    color: var(--color-text-primary);
    font-weight: var(--font-weight-medium, 500);
  }

  .detail {
    font-size: 0.75rem;
  }
</style>
