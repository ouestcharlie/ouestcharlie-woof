<script>
  /**
   * @type {{
   *   match: any,
   *   previewUrl: (match: any) => string | null,
   *   thumbnailUrl: (match: any) => string | null,
   *   onClose: () => void,
   * }}
   */
  let { match, previewUrl, thumbnailUrl, onClose } = $props();

  let imgSrc = $derived(previewUrl(match) ?? thumbnailUrl(match));
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="overlay" onclick={onClose}>
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div class="panel" onclick={(e) => e.stopPropagation()}>
    <button class="close" onclick={onClose}>✕</button>
    {#if imgSrc}
      <img src={imgSrc} alt={match.filename} />
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
    max-width: 90vw;
    max-height: 90vh;
    gap: 0.75rem;
  }

  img {
    max-width: 100%;
    max-height: 80vh;
    object-fit: contain;
    border-radius: 4px;
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
