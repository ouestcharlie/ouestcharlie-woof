<script>
  import { onMount, onDestroy } from 'svelte';
  import * as m from '../paraglide/messages.js';
  import {
    formatDate,
    formatDimensions,
    formatCamera,
    formatAperture,
    formatExposure,
    formatFocal,
    formatDuration,
    codecLabel,
    codecUnplayable,
    formatGps,
    truncate,
  } from '../lib/format.js';

  /**
   * @type {{
   *   matches: any[],
   *   selectedIndex: number,
   *   onNavigate: (index: number) => void,
   *   previewUrl: (match: any) => string | null,
   *   videoUrl?: (match: any) => string | null,
   *   active?: boolean,
   *   isFullscreen?: boolean,
   *   inlineMaxHeight?: number,
   * }}
   */
  let { matches, selectedIndex, onNavigate, previewUrl, videoUrl = () => null, active = true, isFullscreen = false, inlineMaxHeight = 520 } = $props();

  let match = $derived(matches[selectedIndex]);
  let isVideo = $derived(match?.mediaType === 'video');
  // Cover-frame JPEG — the crossfade image for photos, the <video> poster for videos.
  let jpegUrl = $derived(previewUrl(match));
  let videoSrc = $derived(isVideo ? videoUrl(match) : null);

  // Ref to the active <video>, so navigating away can pause it.
  let videoEl = $state(null);

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

  // aspect-ratio driven by the item's natural dimensions.
  // CSS max-width/max-height on .preview-container handle clamping to the viewer bounds,
  // so no JS measurement is needed and the layout reflows automatically on any size change.
  let aspectRatio = $derived(
    match?.width && match?.height ? match.width / match.height : 1
  );

  let hasPrev = $derived(selectedIndex > 0);
  let hasNext = $derived(selectedIndex < matches.length - 1);

  // Pause any playing video before leaving it, otherwise audio keeps playing
  // off-screen after navigation.
  function pauseVideo() { videoEl?.pause(); }

  function prev() { if (hasPrev) { pauseVideo(); onNavigate(selectedIndex - 1); } }
  function next() { if (hasNext) { pauseVideo(); onNavigate(selectedIndex + 1); } }

  // The panel is kept mounted while hidden (to preserve image load state), so
  // ignore arrow keys unless the preview is the active view — otherwise grid
  // browsing would silently advance the selection in the background.
  function onKeydown(e) {
    if (!active) return;
    if (e.key === 'ArrowLeft')  { e.preventDefault(); prev(); }
    if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
  }

  // Caption bar: truncate the description to keep the overlay compact.
  const CAPTION_MAX = 100;

  let captionDescription = $derived(truncate(match?.description, CAPTION_MAX));
  let captionTags = $derived(match?.tags?.slice(0, 5) ?? []);
  let cameraLine = $derived(formatCamera(match));
  let gpsLine = $derived(formatGps(match?.gps));
  let durationLine = $derived(isVideo ? formatDuration(match?.durationSeconds) : null);
  let codecName = $derived(isVideo ? codecLabel(match?.videoCodec) : null);
  let codecWarn = $derived(isVideo && codecUnplayable(match?.videoCodec));

  onMount(() => { window.addEventListener('keydown', onKeydown); });
  onDestroy(() => { window.removeEventListener('keydown', onKeydown); pauseVideo(); });
</script>

<div class="panel">
  <div class="viewer">
    <div class="preview-container" class:fullscreen={isFullscreen} style="aspect-ratio: {aspectRatio}; --inline-max: {inlineMaxHeight}px;">
      {#if isVideo}
        <!--
          Video: <video> with the cover-frame JPEG as poster so the panel shows
          an image instantly while the stream buffers. No autoplay —
          user-initiated playback only.
        -->
        <!-- svelte-ignore a11y_media_has_caption -->
        <video
          bind:this={videoEl}
          class="preview-video"
          src={videoSrc}
          poster={jpegUrl}
          controls
          preload="metadata"
        ></video>
      {:else}
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
      {/if}

      <button class="nav prev" onclick={prev} disabled={!hasPrev}>‹</button>
      <button class="nav next" onclick={next} disabled={!hasNext}>›</button>

      <!-- Info toggle for the details side panel. Hidden while the panel is
           open (the panel has its own close button) so it doesn't overlap the
           panel in narrow-screen bottom-sheet mode. -->
      {#if !panelOpen}
        <button
          class="info-toggle"
          onclick={togglePanel}
          aria-label={m.preview_show_details()}
          aria-expanded="false"
          title={m.preview_details()}
        >ⓘ</button>
      {/if}

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
            <span class="caption-sep" aria-hidden="true">–</span>
            <span class="caption-date">{formatDate(match.dateTaken)}</span>
          {/if}
          {#if durationLine}
            <span class="caption-duration">{durationLine}</span>
          {/if}
        </div>
      </div>

      <!-- Collapsible details side panel (overlay — does not resize the image). -->
      {#if panelOpen}
        <aside class="details">
          <header class="details-head">
            <span>{m.preview_details()}</span>
            <button class="details-close" onclick={togglePanel} aria-label={m.preview_close_details()}>×</button>
          </header>

          <section class="subpane">
            <h3>{m.preview_overview()}</h3>
            {#if match.rating > 0}
              <div class="stars" aria-label={m.preview_rating({ rating: match.rating })}>
                <span aria-hidden="true">{'★'.repeat(match.rating)}{'☆'.repeat(5 - match.rating)}</span>
              </div>
            {/if}
            {#if match.description}
              <div class="field"><span class="field-val desc">{match.description}</span></div>
            {/if}
            {#if match.tags?.length}
              <div class="pills tags">
                {#each match.tags as tag (tag)}
                  <span class="pill">{tag}</span>
                {/each}
              </div>
            {/if}
            {#if match.dateTaken}
              <div class="field"><span class="field-key">{m.field_date()}</span><span class="field-val">{formatDate(match.dateTaken)}</span></div>
            {/if}
            <div class="field"><span class="field-key">{m.field_file()}</span><span class="field-val">{match.filename}</span></div>
            {#if match.partition}
              <div class="field"><span class="field-key">{m.field_partition()}</span><span class="field-val">{match.partition}</span></div>
            {/if}
            {#if formatDimensions(match)}
              <div class="field"><span class="field-key">{m.field_dimensions()}</span><span class="field-val">{formatDimensions(match)}</span></div>
            {/if}
            {#if durationLine}
              <div class="field"><span class="field-key">{m.field_duration()}</span><span class="field-val">{durationLine}</span></div>
            {/if}
          </section>

          {#if isVideo}
            <section class="subpane">
              <h3>{m.preview_video()}</h3>
              {#if codecName}
                <div class="field">
                  <span class="field-key">{m.field_codec()}</span>
                  <span class="field-val">
                    {codecName}
                    {#if codecWarn}
                      <span class="codec-warn">{m.preview_codec_warning({ codec: codecName })}</span>
                    {/if}
                  </span>
                </div>
              {/if}
              {#if match.hasAudio != null}
                <div class="field"><span class="field-key">{m.field_audio()}</span><span class="field-val">{match.hasAudio ? m.preview_audio_yes() : m.preview_audio_no()}</span></div>
              {/if}
              {#if cameraLine}
                <div class="field"><span class="field-key">{m.field_camera()}</span><span class="field-val">{cameraLine}</span></div>
              {/if}
            </section>
          {:else}
          <section class="subpane">
            <h3>{m.preview_camera()}</h3>
            {#if cameraLine}
              <div class="field"><span class="field-key">{m.field_camera()}</span><span class="field-val">{cameraLine}</span></div>
            {/if}
            {#if match.lensModel}
              <div class="field"><span class="field-key">{m.field_lens()}</span><span class="field-val">{match.lensModel}</span></div>
            {/if}
            {#if match.isoSpeed != null}
              <div class="field"><span class="field-key">{m.field_iso()}</span><span class="field-val">{match.isoSpeed}</span></div>
            {/if}
            {#if formatAperture(match.aperture)}
              <div class="field"><span class="field-key">{m.field_aperture()}</span><span class="field-val">{formatAperture(match.aperture)}</span></div>
            {/if}
            {#if formatExposure(match.exposureTime)}
              <div class="field"><span class="field-key">{m.field_exposure()}</span><span class="field-val">{formatExposure(match.exposureTime)}</span></div>
            {/if}
            {#if formatFocal(match)}
              <div class="field"><span class="field-key">{m.field_focal_length()}</span><span class="field-val">{formatFocal(match)}</span></div>
            {/if}
            {#if !cameraLine && !match.lensModel && match.isoSpeed == null && match.aperture == null && match.exposureTime == null && match.focalLength == null}
              <div class="field empty">{m.preview_no_camera()}</div>
            {/if}
          </section>
          {/if}

          <section class="subpane">
            <h3>{m.preview_location()}</h3>
            {#if gpsLine}
              <div class="field"><span class="field-val">{gpsLine}</span></div>
            {:else}
              <div class="field empty">{m.preview_no_location()}</div>
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
    /* Width-driven sizing is load-bearing: the top-down height:100% chain is
       indefinite in the MCP iframe, so width:100% + aspect-ratio is what gives
       the container (and the whole panel) a non-zero height, bottom-up. The
       height therefore needs a max cap so a tall portrait does not overflow. */
    width: 100%;
    max-width: 100%;
    /* Inline (chat-flow): a FIXED px cap (--inline-max, from App's
       INLINE_PREVIEW_MAX = iframe height minus header+status chrome). dvh here
       would track the auto-resizing iframe and feed back into an infinite reflow,
       so the cap must be decoupled from the iframe height. */
    max-height: var(--inline-max, 520px);
  }

  /* Fullscreen: the iframe viewport is the fixed screen, so dvh is stable and
     lets the image use the full height (minus header + status chrome). */
  .preview-container.fullscreen {
    max-height: calc(100dvh - 5rem);
  }

  .preview-img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  .preview-video {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: #000;
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

  /* Nav arrows overlay the image — keep semi-transparent black regardless of theme.
     Centered as a fixed-height band (not full height) so they clear the video
     controls at the bottom and the caption at the top. */
  .nav {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    height: 40%;
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

  .nav.prev { left: 0; border-radius: 0 var(--border-radius-xs, 4px) var(--border-radius-xs, 4px) 0; }
  .nav.next { right: 0; border-radius: var(--border-radius-xs, 4px) 0 0 var(--border-radius-xs, 4px); }

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

  .info-toggle:hover {
    background: rgba(0, 0, 0, 0.6);
  }

  /* Caption bar — scrim + text overlaid at the image top, so it clears the
     video controls at the bottom. Right padding leaves room for the info
     toggle in the top-right corner. */
  .caption {
    position: absolute;
    left: 0;
    right: 0;
    top: 0;
    padding: 0.7rem 3rem 0.8rem 0.8rem;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    color: #fff;
    background: linear-gradient(to bottom, rgba(0, 0, 0, 0.65), rgba(0, 0, 0, 0));
    z-index: 2;
    pointer-events: none;
  }

  .caption-desc {
    font-size: 1rem;
    line-height: 1.35;
  }

  .caption-foot {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 0.75rem;
    font-size: 0.85rem;
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

  /* Inline playability warning next to the codec row. */
  .codec-warn {
    display: block;
    margin-top: 0.15rem;
    font-size: 0.72rem;
    color: #f5a623;
    opacity: 0.9;
  }

  .stars {
    color: #f5c451;
    font-size: 0.95rem;
    letter-spacing: 0.12em;
    line-height: 1;
    margin-bottom: 0.5rem;
  }

  /* Breathing room around the tag pills — more above (after the description)
     than below (before the following fields). */
  .pills.tags {
    margin-top: 0.75rem;
    margin-bottom: 0.5rem;
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
