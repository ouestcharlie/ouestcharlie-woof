/**
 * API client for the Woof HTTP server.
 *
 * Different MCP hosts accept different loopback hostnames in the iframe's CSP
 * (Claude Desktop Chat requires "localhost"; Claude CoWork blocks it and
 * requires "127.0.0.1"). Woof exposes every candidate origin for its port;
 * this module tries each in turn and remembers whichever one actually works,
 * so callers never build server URLs themselves.
 */

let candidates = $state([]);
let resolvedOrigin = $state(null);

/**
 * Set (or refresh) the candidate origin list. Safe to call more than once —
 * e.g. once from embedded page data at mount, and again from each MCP tool
 * result in case the server restarted on a new port.
 * @param {string[]} origins
 */
export function initServerOrigins(origins) {
  candidates = origins ?? [];
  if (resolvedOrigin === null || !candidates.includes(resolvedOrigin)) {
    resolvedOrigin = candidates[0] ?? null;
  }
}

/** Currently known-working origin, or the first untried candidate. Reactive. */
export function getResolvedOrigin() {
  return resolvedOrigin;
}

async function request(path, options) {
  const tryOrder = resolvedOrigin
    ? [resolvedOrigin, ...candidates.filter((origin) => origin !== resolvedOrigin)]
    : candidates;

  let lastError;
  for (const origin of tryOrder) {
    try {
      const url = `${origin}${path}`;
      const response = options !== undefined ? await fetch(url, options) : await fetch(url);
      if (!response.ok) throw new Error(response.statusText);
      resolvedOrigin = origin;
      return response;
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError ?? new Error('No server origin available');
}

export async function fetchResults(token) {
  const response = await request(`/api/results/${token}`);
  return response.json();
}

export async function fetchResultsPage(token, page) {
  const response = await request(`/api/results/${token}/page/${page}`);
  return response.json();
}

export async function fetchIndexingStatus(sessionId) {
  const response = await request(`/api/indexing/${sessionId}`);
  return response.json();
}

export async function cancelIndexing(sessionId) {
  await request(`/api/indexing/${sessionId}/cancel`, { method: 'POST' });
}

function encodePartition(partition) {
  return partition.split('/').map(encodeURIComponent).join('/');
}

/** URL for a match's proxied AVIF thumbnail grid, or null if unavailable. */
export function thumbnailUrl(match) {
  if (!resolvedOrigin || !match?.library || !match?.avifHash || match?.tileIndex == null) return null;
  return `${resolvedOrigin}/thumbnail/${encodeURIComponent(match.library)}/${encodePartition(match.partition)}/${encodeURIComponent(match.avifHash)}`;
}

/** URL for a match's on-demand JPEG preview, or null if unavailable. */
export function previewUrl(match) {
  if (!resolvedOrigin || !match?.library || !match?.contentHash) return null;
  return `${resolvedOrigin}/previews/${encodeURIComponent(match.library)}/${encodePartition(match.partition)}/${encodeURIComponent(match.contentHash)}.jpg`;
}
