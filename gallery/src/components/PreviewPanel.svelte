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

  // Details side panel — overlay on top of the image, collapsed by default.
  let panelOpen = $state(false);
  function togglePanel() { panelOpen = !panelOpen; }

  // shownUrl: the last fully-loaded URL, kept visible while the next image loads.
  // jpegUrl becomes shownUrl only once the img fires onload, avoiding flicker.
  let shownUrl = $state(null);
  let previewLoaded = $state(false);
  // showSpinner is delayed so it only appears if loading takes more than ~300ms.
  let showSpinner = $state(false);
  let spinnerTimer = null;
  $effect(() => {
    jpegUrl;
    previewLoaded = false;
    showSpinner = false;
    clearTimeout(spinnerTimer);
    spinnerTimer = setTimeout(() => { if (!previewLoaded) showSpinner = true; }, 300);
  });

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

  // --- Field formatting helpers (all return null when the value is absent) ---

  function formatDimensions(m) {
    return m?.width && m?.height ? `${m.width} × ${m.height}` : null;
  }

  function formatCamera(m) {
    const parts = [m?.make, m?.model].filter(Boolean);
    return parts.length ? parts.join(' ') : null;
  }

  // EXIF values arrive as rationals decoded to floats, so they carry noise
  // (e.g. 1.7999999523 or 5.5399999). Round to `decimals` and drop any
  // trailing zeros so "8.0" → "8" and "5.50" → "5.5".
  function roundTrim(v, decimals = 1) {
    return parseFloat(v.toFixed(decimals)).toString();
  }

  function formatAperture(v) {
    return v != null ? `f/${roundTrim(v)}` : null;
  }

  // Exposure time in seconds → "1/250 s" for sub-second, "2 s" otherwise.
  function formatExposure(v) {
    if (v == null) return null;
    if (v >= 1) return `${roundTrim(v)} s`;
    return `1/${Math.round(1 / v)} s`;
  }

  function formatFocal(m) {
    if (m?.focalLength == null) return null;
    let s = `${roundTrim(m.focalLength)} mm`;
    if (m.focalLength35mm != null) s += ` (${Math.round(m.focalLength35mm)} mm eq.)`;
    return s;
  }

  // GPS arrives as [lat, lon]; render with a fixed precision and hemisphere.
  function formatGps(gps) {
    if (!Array.isArray(gps) || gps.length !== 2) return null;
    const [lat, lon] = gps;
    if (lat == null || lon == null) return null;
    const ns = lat >= 0 ? 'N' : 'S';
    const ew = lon >= 0 ? 'E' : 'W';
    return `${Math.abs(lat).toFixed(5)}° ${ns}, ${Math.abs(lon).toFixed(5)}° ${ew}`;
  }

  // Caption bar: truncate the description to keep the overlay compact.
  const CAPTION_MAX = 100;
  function truncate(text, max) {
    if (!text) return null;
    return text.length > max ? `${text.slice(0, max)}…` : text;
  }

  let captionDescription = $derived(truncate(match?.description, CAPTION_MAX));
  let captionTags = $derived(match?.tags?.slice(0, 5) ?? []);
  let cameraLine = $derived(formatCamera(match));
  let gpsLine = $derived(formatGps(match?.gps));

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
          onload={() => { previewLoaded = true; showSpinner = false; shownUrl = jpegUrl; }}
          alt={match.filename}
        />
      {/if}

      {#if showSpinner}
        <div class="loading-overlay" class:dim={!!shownUrl}>
          <div class="spinner"></div>
        </div>
      {/if}

      <button class="nav prev" onclick={prev} disabled={!hasPrev}>‹</button>
      <button class="nav next" onclick={next} disabled={!hasNext}>›</button>

      <!-- Info toggle for the details side panel -->
      <button
        class="info-toggle"
        class:active={panelOpen}
        onclick={togglePanel}
        aria-label={panelOpen ? 'Hide details' : 'Show details'}
        aria-expanded={panelOpen}
        title="Details"
      >ⓘ</button>

      <!-- Caption bar overlaid at the bottom of the image. -->
      <div class="caption">
        {#if captionDescription}
          <div class="caption-desc">{captionDescription}</div>
        {/if}
        {#if captionTags.length}
          <div class="pills">
            {#each captionTags as tag (tag)}
              <span class="pill">{tag}</span>
            {/each}
          </div>
        {/if}
        <div class="caption-foot">
          <span class="caption-filename">{match.filename}</span>
          {#if match.dateTaken}
            <span class="caption-date">{formatDate(match.dateTaken)}</span>
          {/if}
        </div>
      </div>

      <!-- Collapsible details side panel (overlay — does not resize the image). -->
      {#if panelOpen}
        <aside class="details">
          <header class="details-head">
            <span>Details</span>
            <button class="details-close" onclick={togglePanel} aria-label="Close details">×</button>
          </header>

          <section class="subpane">
            <h3>Overview</h3>
            {#if match.description}
              <div class="field"><span class="field-val desc">{match.description}</span></div>
            {/if}
            {#if match.tags?.length}
              <div class="pills">
                {#each match.tags as tag (tag)}
                  <span class="pill">{tag}</span>
                {/each}
              </div>
            {/if}
            {#if match.dateTaken}
              <div class="field"><span class="field-key">Date</span><span class="field-val">{formatDate(match.dateTaken)}</span></div>
            {/if}
            <div class="field"><span class="field-key">File</span><span class="field-val">{match.filename}</span></div>
            {#if match.partition}
              <div class="field"><span class="field-key">Partition</span><span class="field-val">{match.partition}</span></div>
            {/if}
            {#if formatDimensions(match)}
              <div class="field"><span class="field-key">Dimensions</span><span class="field-val">{formatDimensions(match)}</span></div>
            {/if}
          </section>

          <section class="subpane">
            <h3>Camera</h3>
            {#if cameraLine}
              <div class="field"><span class="field-key">Camera</span><span class="field-val">{cameraLine}</span></div>
            {/if}
            {#if match.lensModel}
              <div class="field"><span class="field-key">Lens</span><span class="field-val">{match.lensModel}</span></div>
            {/if}
            {#if match.isoSpeed != null}
              <div class="field"><span class="field-key">ISO</span><span class="field-val">{match.isoSpeed}</span></div>
            {/if}
            {#if formatAperture(match.aperture)}
              <div class="field"><span class="field-key">Aperture</span><span class="field-val">{formatAperture(match.aperture)}</span></div>
            {/if}
            {#if formatExposure(match.exposureTime)}
              <div class="field"><span class="field-key">Exposure</span><span class="field-val">{formatExposure(match.exposureTime)}</span></div>
            {/if}
            {#if formatFocal(match)}
              <div class="field"><span class="field-key">Focal length</span><span class="field-val">{formatFocal(match)}</span></div>
            {/if}
            {#if !cameraLine && !match.lensModel && match.isoSpeed == null && match.aperture == null && match.exposureTime == null && match.focalLength == null}
              <div class="field empty">No camera data</div>
            {/if}
          </section>

          <section class="subpane">
            <h3>Location</h3>
            {#if gpsLine}
              <div class="field"><span class="field-val">{gpsLine}</span></div>
            {:else}
              <div class="field empty">No location data</div>
            {/if}
          </section>
        </aside>
      {/if}
    </div>
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

  /* When a previous image is already shown, use a translucent overlay instead */
  .loading-overlay.dim {
    background: rgba(0, 0, 0, 0.5);
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

  /* Info toggle — overlays the top-right corner, above nav arrows. */
  .info-toggle {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    width: 2rem;
    height: 2rem;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.35);
    border: none;
    border-radius: 50%;
    color: #fff;
    font-size: 1.1rem;
    line-height: 1;
    cursor: pointer;
    transition: background 0.15s;
    z-index: 3;
  }

  .info-toggle:hover, .info-toggle.active {
    background: rgba(0, 0, 0, 0.6);
  }

  /* Caption bar — scrim + text overlaid at the image bottom. */
  .caption {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    padding: 0.6rem 0.8rem 0.7rem;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    color: #fff;
    background: linear-gradient(to top, rgba(0, 0, 0, 0.65), rgba(0, 0, 0, 0));
    z-index: 2;
    pointer-events: none;
  }

  .caption-desc {
    font-size: 0.85rem;
    line-height: 1.3;
  }

  .caption-foot {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 0.75rem;
    font-size: 0.75rem;
    opacity: 0.9;
  }

  .caption-filename {
    font-weight: var(--font-weight-medium, 500);
  }

  /* Pills — shared by caption bar and Overview subpane. */
  .pills {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }

  .pill {
    display: inline-block;
    padding: 0.1rem 0.55rem;
    font-size: 0.72rem;
    line-height: 1.4;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.18);
    color: #fff;
    white-space: nowrap;
  }

  /* Details side panel — right rail overlay by default. */
  .details {
    position: absolute;
    top: 0;
    right: 0;
    bottom: 0;
    width: min(320px, 85%);
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 0.75rem 0.9rem 1rem;
    overflow-y: auto;
    color: #fff;
    background: rgba(0, 0, 0, 0.72);
    backdrop-filter: blur(4px);
    z-index: 4;
  }

  .details-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-weight: var(--font-weight-medium, 500);
    font-size: 0.9rem;
    padding-bottom: 0.25rem;
  }

  .details-close {
    background: none;
    border: none;
    color: #fff;
    font-size: 1.4rem;
    line-height: 1;
    cursor: pointer;
    padding: 0 0.2rem;
  }

  .subpane {
    padding: 0.5rem 0;
    border-top: 1px solid rgba(255, 255, 255, 0.15);
  }

  .subpane h3 {
    margin: 0 0 0.4rem;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    opacity: 0.65;
  }

  .field {
    display: flex;
    gap: 0.5rem;
    font-size: 0.8rem;
    line-height: 1.4;
    padding: 0.1rem 0;
  }

  .field-key {
    flex-shrink: 0;
    width: 6.5rem;
    opacity: 0.6;
  }

  .field-val {
    word-break: break-word;
  }

  .field-val.desc {
    line-height: 1.4;
  }

  .field.empty {
    opacity: 0.5;
    font-style: italic;
  }

  /* Narrow screens: side panel becomes a full-width bottom sheet. */
  @media (max-width: 600px) {
    .details {
      top: auto;
      left: 0;
      width: auto;
      max-height: 70%;
      border-top-left-radius: var(--border-radius-xs, 4px);
      border-top-right-radius: var(--border-radius-xs, 4px);
    }
  }
</style>
