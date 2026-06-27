<script>
  import { onMount, onDestroy } from 'svelte';

  /**
   * @type {{
   *   matches: any[],
   *   selectedIndex: number,
   *   onNavigate: (index: number) => void,
   *   previewUrl: (match: any) => string | null,
   * }}
   */
  let { matches, selectedIndex, onNavigate, previewUrl } = $props();

  let match = $derived(matches[selectedIndex]);
  let jpegUrl = $derived(previewUrl(match));

  // shownUrl: the last fully-loaded URL, kept visible while the next image loads.
  // jpegUrl becomes shownUrl only once the img fires onload, avoiding flicker.
  let shownUrl = $state(null);
  let previewLoaded = $state(false);
  $effect(() => { jpegUrl; previewLoaded = false; });

  // aspect-ratio driven by the photo's natural dimensions.
  // CSS max-width/max-height on .preview-container handle clamping to the viewer bounds,
  // so no JS measurement is needed and the layout reflows automatically on any size change.
  let aspectRatio = $derived(
    match?.width && match?.height ? match.width / match.height : 1
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

  onMount(() => { window.addEventListener('keydown', onKeydown); });
  onDestroy(() => { window.removeEventListener('keydown', onKeydown); });
</script>

<div class="panel">
  <div class="viewer">
    <div class="preview-container" style="aspect-ratio: {aspectRatio};">
      <!-- Previous image stays visible underneath while the next one loads. -->
      {#if shownUrl}
        <img src={shownUrl} class="preview-img" alt="" aria-hidden="true" />
      {/if}

      <!--
        Incoming image. Always in DOM (when jpegUrl is available) so the browser
        fetches it and fires onload reliably — display:none suppresses onload
        in some sandboxed environments (e.g. Claude Desktop iframe).
        Fades in once loaded, then becomes the new shownUrl.
      -->
      {#if jpegUrl}
        <img
          src={jpegUrl}
          class="preview-img incoming"
          class:loaded={previewLoaded}
          onload={() => { previewLoaded = true; shownUrl = jpegUrl; }}
          alt={match.filename}
        />
      {/if}

      <!-- Spinner shown on first load only (no previous image to display). -->
      {#if !previewLoaded && !shownUrl}
        <div class="loading-overlay">
          <div class="spinner"></div>
        </div>
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
    gap: 0.75rem;
  }

  .viewer {
    display: flex;
    flex: 1;
    min-height: 0;
    overflow: hidden;
    align-items: center;
    justify-content: center;
  }

  .preview-container {
    position: relative;
    overflow: hidden;
    border-radius: var(--border-radius-xs, 4px);
    flex-shrink: 0;
    background: var(--color-background-secondary);
    max-width: 100%;
    max-height: 100%;
    /* width/height resolved by CSS from aspect-ratio + max constraints */
    width: 100%;
    /*height: 100%;*/
  }

  .preview-img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  .preview-img.incoming {
    opacity: 0;
  }

  .preview-img.incoming.loaded {
    opacity: 1;
    transition: opacity 0.25s ease-in;
  }

  .loading-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--color-background-secondary);
  }

  .spinner {
    width: 2rem;
    height: 2rem;
    border: 2px solid var(--color-border-primary);
    border-top-color: var(--color-text-secondary);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
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
    flex-shrink: 0;
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
