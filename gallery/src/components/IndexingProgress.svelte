<script>
  import { onMount, onDestroy } from 'svelte';

  let { serverUrl, sessionId, library, partition, mcpApp, mcpReady = false } = $props();

  let status = $state('running');
  let progress = $state(0);
  let total = $state(1);
  let message = $state('');
  let summary = $state(null);
  let error = $state(null);
  let contextSent = $state(false);
  let stopping = $state(false);

  let pollInterval = null;

  async function poll() {
    if (!serverUrl || !sessionId) return;
    try {
      const resp = await fetch(`${serverUrl}/api/indexing/${sessionId}`);
      if (!resp.ok) return;
      const data = await resp.json();
      status = data.status ?? 'running';
      progress = data.progress ?? 0;
      total = data.total ?? 1;
      message = data.message ?? '';
      summary = data.summary ?? null;
      error = data.error ?? null;

      if (status !== 'running' && status !== 'cancelling') {
        clearInterval(pollInterval);
        pollInterval = null;
        // handleDone is triggered by $effect once mcpReady is also true
      }
    } catch {
      // transient network error — keep polling
    }
  }

  // Fire handleDone only when BOTH the status is terminal AND the MCP connection
  // has completed its handshake. Without mcpReady, postMessages arrive before
  // Claude Desktop has registered this iframe as a trusted source, causing
  // "Ignoring message from unknown source" errors.
  $effect(() => {
    if ((status === 'completed' || status === 'failed' || status === 'cancelled') && (mcpReady || !mcpApp)) {
      handleDone();
    }
  });

  async function stopIndexing() {
    stopping = true;
    try {
      await fetch(`${serverUrl}/api/indexing/${sessionId}/cancel`, { method: 'POST' });
    } catch {
      // poll will reflect the new status
    }
  }

  async function handleDone() {
    if (contextSent) return;
    contextSent = true;
    if (!mcpApp) return;

    if (status === 'cancelled') {
      return; // no MCP turn for user-initiated stop
    }

    if (status === 'completed') {
      try {
        const summaryMarkdown = formatSummaryMarkdown(summary);
        await mcpApp.updateModelContext({
          content: [{ type: 'text', text: summaryMarkdown }],
        });
        // updateModelContext stores context for next turn but does not trigger the model.
        // sendMessage triggers a new turn so Claude receives the summary immediately.
        await mcpApp.sendMessage({
          role: 'user',
          content: [{ type: 'text', text: 'Indexing complete.' }],
        });
      } catch {
        // best-effort
      }
    } else if (status === 'failed') {
      try {
        const errText = `Indexing failed: ${error ?? 'unknown error'}`;
        await mcpApp.updateModelContext({
          content: [{ type: 'text', text: errText }],
        });
        await mcpApp.sendMessage({
          role: 'user',
          content: [{ type: 'text', text: 'Indexing failed.' }],
        });
      } catch {
        // best-effort
      }
    }
  }

  function formatSummaryMarkdown(s) {
    const lines = [`Indexing complete for **${library}${partition ? ' / ' + partition : ''}**.`];
    if (s?.totalPhotosProcessed !== undefined) lines.push(`- Photos processed: ${s.totalPhotosProcessed}`);
    if (s?.totalSidecarsCreated !== undefined) lines.push(`- Sidecars created: ${s.totalSidecarsCreated}`);
    if (s?.totalThumbnailsRebuilt !== undefined) lines.push(`- Thumbnail batches rebuilt: ${s.totalThumbnailsRebuilt}`);
    if (s?.totalErrors !== undefined && s.totalErrors > 0) lines.push(`- Errors: ${s.totalErrors}`);
    if (s?.totalDurationMs !== undefined) lines.push(`- Duration: ${(s.totalDurationMs / 1000).toFixed(1)}s`);
    return lines.join('\n');
  }

  onMount(() => {
    poll();
    pollInterval = setInterval(poll, 1000);
  });

  onDestroy(() => {
    if (pollInterval !== null) clearInterval(pollInterval);
  });

  let progressPct = $derived(total > 0 ? Math.min(100, (progress / total) * 100) : 0);
</script>

<div class="indexing">
  <header class="indexing-header">
    <h1>Indexing {library}{partition ? ' / ' + partition : ''}</h1>
    <div class="header-right">
      <span class="indexing-status" class:running={status === 'running'} class:cancelling={status === 'cancelling'} class:cancelled={status === 'cancelled'} class:completed={status === 'completed'} class:failed={status === 'failed'}>
        {status}
      </span>
    </div>
  </header>

  {#if status === 'running' || status === 'cancelling'}
    <div class="progress-section">
      <progress value={progress} max={total}></progress>
      <div class="progress-label">{Math.round(progressPct)}% — {Math.round(progress)} / {Math.round(total)}</div>
      {#if message}
        <div class="progress-message">{message}</div>
      {/if}
    </div>
    <div class="stop-row">
      <button class="stop-btn" onclick={stopIndexing} disabled={stopping}>Stop</button>
    </div>
  {/if}

  {#if status === 'completed'}
    <div class="summary-card">
      <div class="summary-title">Indexing complete 
        {#if summary.totalDurationMs !== undefined}
            <span>in {(summary.totalDurationMs / 1000).toFixed(1)}s</span>
        {/if}
      </div>
      {#if summary}
        <ul class="summary-list">          
          {#if summary.totalPhotosProcessed !== undefined}
            <li>Photos processed: <strong>{summary.totalPhotosProcessed}</strong></li>
          {/if}
          {#if summary.totalSidecarsCreated !== undefined}
            <li>Sidecars created: <strong>{summary.totalSidecarsCreated}</strong></li>
          {/if}
          {#if summary.totalThumbnailsRebuilt !== undefined}
            <li>Thumbnail batches rebuilt: <strong>{summary.totalThumbnailsRebuilt}</strong></li>
          {/if}
          {#if summary.totalErrors !== undefined && summary.totalErrors > 0}
            <li class="error-count">
              <details>
                <summary>Errors: <strong>{summary.totalErrors}</strong></summary>
                {#if summary.topErrorDetails?.length}
                  <ul class="error-details">
                    {#each summary.topErrorDetails as detail, i (i)}
                      <li>{detail}</li>
                    {/each}
                  </ul>
                {/if}
              </details>
            </li>
          {/if}         
        </ul>
      {:else}
        <p class="summary-empty">No summary available.</p>
      {/if}
    </div>
  {/if}

  {#if status === 'cancelled'}
    <div class="cancelled-card">
      <div class="cancelled-title">Indexing stopped</div>
    </div>
  {/if}

  {#if status === 'failed'}
    <div class="error-card">
      <div class="error-title">Indexing failed</div>
      <p class="error-message">{error ?? 'Unknown error'}</p>
    </div>
  {/if}
</div>

<style>
  .indexing {
    display: flex;
    flex-direction: column;
    height: 100%;
    padding: 1.5rem;
    background: var(--color-background-tertiary);
    color: var(--color-text-primary);
    font-family: var(--font-sans, system-ui, sans-serif);
    box-sizing: border-box;
  }

  .indexing-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1.5rem;
    padding-bottom: 0.75rem;
    border-bottom: var(--border-width-regular, 0.5px) solid var(--color-border-primary);
  }

  .indexing-header h1 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: var(--font-weight-semibold, 600);
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-shrink: 0;
  }

  .stop-row {
    margin-top: auto;
    display: flex;
    justify-content: flex-end;
    padding-top: 1rem;
  }

  .stop-btn {
    font-size: 0.85rem;
    padding: 0.4rem 1.1rem;
    border-radius: var(--border-radius-xs, 4px);
    border: var(--border-width-regular, 0.5px) solid rgba(244, 67, 54, 0.5);
    background: transparent;
    color: #f44336;
    cursor: pointer;
    font-weight: var(--font-weight-semibold, 600);
    width: fit-content;
  }

  .stop-btn:hover:not(:disabled) {
    background: rgba(244, 67, 54, 0.1);
  }

  .stop-btn:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .indexing-status {
    font-size: 0.75rem;
    padding: 0.2rem 0.5rem;
    border-radius: var(--border-radius-xs, 4px);
    font-weight: var(--font-weight-semibold, 600);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .indexing-status.running {
    background: var(--color-accent-secondary, #1a3a5c);
    color: var(--color-accent-primary, #4fc3f7);
  }

  .indexing-status.cancelling {
    background: rgba(255, 167, 38, 0.15);
    color: #ffa726;
  }

  .indexing-status.cancelled {
    background: rgba(158, 158, 158, 0.15);
    color: #9e9e9e;
  }

  .indexing-status.completed {
    background: rgba(76, 175, 80, 0.15);
    color: #4caf50;
  }

  .indexing-status.failed {
    background: rgba(244, 67, 54, 0.15);
    color: #f44336;
  }

  .progress-section {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  progress {
    width: 100%;
    height: 0.5rem;
    appearance: none;
    border: none;
    border-radius: 4px;
    background: var(--color-background-secondary);
    overflow: hidden;
  }

  progress::-webkit-progress-bar {
    background: var(--color-background-secondary);
    border-radius: 4px;
  }

  progress::-webkit-progress-value {
    background: var(--color-accent-primary, #4fc3f7);
    border-radius: 4px;
    transition: width 0.3s ease;
  }

  progress::-moz-progress-bar {
    background: var(--color-accent-primary, #4fc3f7);
    border-radius: 4px;
  }

  .progress-label {
    font-size: 0.8rem;
    color: var(--color-text-secondary);
  }

  .progress-message {
    font-size: 0.8rem;
    color: var(--color-text-tertiary);
    font-family: var(--font-mono, monospace);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .summary-card {
    background: var(--color-background-surface, var(--color-background-secondary));
    border: var(--border-width-regular, 0.5px) solid var(--color-border-primary);
    border-radius: var(--border-radius-sm, 6px);
    padding: 1rem 1.25rem;
  }

  .summary-title {
    font-weight: var(--font-weight-semibold, 600);
    margin-bottom: 0.75rem;
    color: #4caf50;
  }

  .summary-list {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  .summary-list li {
    font-size: 0.9rem;
    color: var(--color-text-secondary);
  }

  .summary-list .error-count {
    color: #f44336;
  }

  .summary-list .error-count details summary {
    cursor: pointer;
    list-style: none;
    display: flex;
    gap: 0.3rem;
    align-items: baseline;
  }

  .summary-list .error-count details summary::before {
    content: '▶';
    font-size: 0.6rem;
    transition: transform 0.15s ease;
  }

  .summary-list .error-count details[open] summary::before {
    transform: rotate(90deg);
  }

  .error-details {
    margin: 0.4rem 0 0 1rem;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }

  .error-details li {
    font-size: 0.78rem;
    font-family: var(--font-mono, monospace);
    color: var(--color-text-secondary);
    word-break: break-all;
  }

  .summary-empty {
    margin: 0;
    font-size: 0.85rem;
    color: var(--color-text-tertiary);
  }

  .error-card {
    background: rgba(244, 67, 54, 0.08);
    border: var(--border-width-regular, 0.5px) solid rgba(244, 67, 54, 0.3);
    border-radius: var(--border-radius-sm, 6px);
    padding: 1rem 1.25rem;
  }

  .error-title {
    font-weight: var(--font-weight-semibold, 600);
    color: #f44336;
    margin-bottom: 0.5rem;
  }

  .error-message {
    margin: 0;
    font-size: 0.85rem;
    color: var(--color-text-secondary);
    font-family: var(--font-mono, monospace);
  }

  .cancelled-card {
    background: var(--color-background-surface, var(--color-background-secondary));
    border: var(--border-width-regular, 0.5px) solid var(--color-border-primary);
    border-radius: var(--border-radius-sm, 6px);
    padding: 1rem 1.25rem;
  }

  .cancelled-title {
    font-weight: var(--font-weight-semibold, 600);
    color: #9e9e9e;
  }
</style>
