<script>
  import { onMount, onDestroy } from 'svelte';

  let { serverUrl, sessionId, library, partition, mcpApp } = $props();

  let status = $state('running');
  let progress = $state(0);
  let total = $state(1);
  let message = $state('');
  let summary = $state(null);
  let error = $state(null);
  let contextSent = $state(false);

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

      if (status !== 'running') {
        clearInterval(pollInterval);
        pollInterval = null;
        await handleDone();
      }
    } catch {
      // transient network error — keep polling
    }
  }

  async function handleDone() {
    if (contextSent) return;
    contextSent = true;
    if (!mcpApp) return;

    if (status === 'completed') {
      try {
        const result = await mcpApp.callServerTool({
          name: 'get_index_result',
          arguments: { session_id: sessionId },
        });
        const text = (result?.content ?? []).find(b => b.type === 'text')?.text;
        const summaryMarkdown = text ? formatSummaryMarkdown(text) : formatSummaryMarkdown(null);
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

  function formatSummaryMarkdown(rawText) {
    if (!rawText) {
      return summary
        ? `Indexing complete for **${library}**.\n\`\`\`json\n${JSON.stringify(summary, null, 2)}\n\`\`\``
        : `Indexing complete for **${library}**.`;
    }
    try {
      const data = JSON.parse(rawText);
      const s = data.summary ?? data;
      const lines = [`Indexing complete for **${library}${partition ? ' / ' + partition : ''}**.`];
      if (s.photosIndexed !== undefined) lines.push(`- Photos indexed: ${s.photosIndexed}`);
      if (s.sidecarsUpdated !== undefined) lines.push(`- Sidecars updated: ${s.sidecarsUpdated}`);
      if (s.thumbnailsGenerated !== undefined) lines.push(`- Thumbnails generated: ${s.thumbnailsGenerated}`);
      if (s.errors !== undefined && s.errors > 0) lines.push(`- Errors: ${s.errors}`);
      return lines.join('\n');
    } catch {
      return rawText;
    }
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
    <span class="indexing-status" class:running={status === 'running'} class:completed={status === 'completed'} class:failed={status === 'failed'}>
      {status}
    </span>
  </header>

  {#if status === 'running'}
    <div class="progress-section">
      <progress value={progress} max={total}></progress>
      <div class="progress-label">{Math.round(progressPct)}% — {Math.round(progress)} / {Math.round(total)}</div>
      {#if message}
        <div class="progress-message">{message}</div>
      {/if}
    </div>
  {/if}

  {#if status === 'completed'}
    <div class="summary-card">
      <div class="summary-title">Indexing complete</div>
      {#if summary}
        <ul class="summary-list">
          {#if summary.photosIndexed !== undefined}
            <li>Photos indexed: <strong>{summary.photosIndexed}</strong></li>
          {/if}
          {#if summary.sidecarsUpdated !== undefined}
            <li>Sidecars updated: <strong>{summary.sidecarsUpdated}</strong></li>
          {/if}
          {#if summary.thumbnailsGenerated !== undefined}
            <li>Thumbnails generated: <strong>{summary.thumbnailsGenerated}</strong></li>
          {/if}
          {#if summary.errors !== undefined && summary.errors > 0}
            <li class="error-count">Errors: <strong>{summary.errors}</strong></li>
          {/if}
        </ul>
      {:else}
        <p class="summary-empty">No summary available.</p>
      {/if}
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
</style>
