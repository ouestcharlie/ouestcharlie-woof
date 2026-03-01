<script>
  /** @type {{ onSearch: (params: Record<string, unknown>) => void }} */
  let { onSearch } = $props();

  let dateMin = $state('');
  let dateMax = $state('');
  let tags = $state('');
  let ratingMin = $state('');
  let ratingMax = $state('');
  let make = $state('');
  let model = $state('');

  function handleSubmit(e) {
    e.preventDefault();
    const params = {};
    if (dateMin.trim()) params.date_min = dateMin.trim();
    if (dateMax.trim()) params.date_max = dateMax.trim();
    if (tags.trim()) {
      params.tags = tags.split(',').map((t) => t.trim()).filter(Boolean);
    }
    if (ratingMin.trim()) params.rating_min = parseInt(ratingMin, 10);
    if (ratingMax.trim()) params.rating_max = parseInt(ratingMax, 10);
    if (make.trim()) params.make = make.trim();
    if (model.trim()) params.model = model.trim();
    onSearch(params);
  }
</script>

<form class="search" onsubmit={handleSubmit}>
  <div class="row">
    <input bind:value={dateMin} type="text" placeholder="From (e.g. 2024-07)" />
    <input bind:value={dateMax} type="text" placeholder="To" />
    <input bind:value={tags} type="text" placeholder="Tags (comma-separated)" />
  </div>
  <div class="row">
    <input bind:value={ratingMin} type="number" min="-1" max="5" placeholder="Rating ≥" />
    <input bind:value={ratingMax} type="number" min="-1" max="5" placeholder="Rating ≤" />
    <input bind:value={make} type="text" placeholder="Camera make" />
    <input bind:value={model} type="text" placeholder="Camera model" />
    <button type="submit">Search</button>
  </div>
</form>

<style>
  .search {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    padding: 0.75rem 1rem;
    background: #1a1a1a;
    border-bottom: 1px solid #333;
  }

  .row {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  input {
    flex: 1;
    min-width: 100px;
    padding: 0.35rem 0.6rem;
    background: #222;
    border: 1px solid #444;
    color: #eee;
    border-radius: 4px;
    font-size: 0.85rem;
  }

  input[type="number"] {
    flex: 0 0 80px;
  }

  button {
    padding: 0.35rem 1rem;
    background: #2563eb;
    color: #fff;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.85rem;
  }

  button:hover {
    background: #1d4ed8;
  }
</style>
