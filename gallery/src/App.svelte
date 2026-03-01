<script>
  import { onMount } from 'svelte';
  import { callTool, onInitialize } from './lib/bridge.js';
  import SearchForm from './components/SearchForm.svelte';
  import PhotoGrid from './components/PhotoGrid.svelte';
  import PreviewPanel from './components/PreviewPanel.svelte';

  let httpPort = $state(null);
  let backendName = $state(null);
  let matches = $state([]);
  let status = $state('Waiting for initialization…');
  let selectedIndex = $state(null);

  onMount(() => {
    onInitialize((data) => {
      httpPort = data.httpPort;
      backendName = data.backend;
      status = `Backend: ${backendName}`;
    });
  });

  async function handleSearch(params) {
    status = 'Searching…';
    try {
      const result = await callTool('search_photos', {
        backend_name: backendName,
        ...params,
      });
      matches = result.matches ?? [];
      status = `${matches.length} photos (${result.partitionsScanned ?? 0} partitions scanned)`;
    } catch (err) {
      status = `Error: ${err.message}`;
    }
  }

  function thumbnailUrl(match) {
    if (!httpPort || !match.thumbnailsPath) return null;
    return `http://127.0.0.1:${httpPort}/thumbnails/${backendName}/${match.partition}/thumbnails.avif`;
  }

  function previewUrl(match) {
    if (!httpPort || !match.previewsPath) return null;
    return `http://127.0.0.1:${httpPort}/previews/${backendName}/${match.partition}/previews.avif`;
  }
</script>

<div class="app">
  <header>
    <h1>OuEstCharlie</h1>
  </header>

  <SearchForm onSearch={handleSearch} />

  <PhotoGrid
    {matches}
    {thumbnailUrl}
    onSelect={(i) => (selectedIndex = i)}
  />

  <div class="status">{status}</div>

  {#if selectedIndex !== null}
    <PreviewPanel
      match={matches[selectedIndex]}
      {previewUrl}
      {thumbnailUrl}
      onClose={() => (selectedIndex = null)}
    />
  {/if}
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: #111;
    color: #eee;
    font-family: system-ui, sans-serif;
  }

  header {
    padding: 0.75rem 1rem;
    background: #1a1a1a;
    border-bottom: 1px solid #333;
  }

  header h1 {
    margin: 0;
    font-size: 1.1rem;
    font-weight: 600;
  }

  .status {
    padding: 0.4rem 1rem;
    font-size: 0.8rem;
    color: #888;
    background: #1a1a1a;
    border-top: 1px solid #333;
  }
</style>
