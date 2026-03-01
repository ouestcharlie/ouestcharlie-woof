<script>
  /**
   * @type {{
   *   matches: any[],
   *   thumbnailUrl: (match: any) => string | null,
   *   onSelect: (index: number) => void,
   * }}
   */
  let { matches, thumbnailUrl, onSelect } = $props();
</script>

<div class="grid">
  {#each matches as match, i}
    {@const url = thumbnailUrl(match)}
    {#if url}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <img
        class="tile"
        src={url}
        alt={match.filename}
        title={match.filename}
        onclick={() => onSelect(i)}
      />
    {:else}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <div role="button" tabindex="0" class="tile placeholder" onclick={() => onSelect(i)}>
        {match.filename}
      </div>
    {/if}
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
    object-fit: cover;
    cursor: pointer;
    border-radius: 4px;
    background: #222;
  }

  .placeholder {
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
