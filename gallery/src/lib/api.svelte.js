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
let sessionId = $state(null);

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

/**
 * Set (or refresh) the gallery/indexing session id used to authenticate and scope
 * requests against Woof's HTTP-mode server. A no-op / null in stdio mode, where
 * routes are unauthenticated.
 * @param {string | null | undefined} id
 */
export function initSessionId(id) {
  sessionId = id ?? null;
}

/** Currently known-working origin, or the first untried candidate. Reactive. */
export function getResolvedOrigin() {
  return resolvedOrigin;
}

/** Path prefix scoping media requests to the current gallery session.
 * Media lives under the gallery family: `/gallery/{sessionId}/media/…`; the session
 * id both authenticates and scopes the request.
 * Empty when unauthenticated (stdio/test), where routes carry no session prefix. */
function mediaPrefix() {
  return sessionId ? `/gallery/${encodeURIComponent(sessionId)}/media` : '';
}

async function request(path, options) {
  const tryOrder = resolvedOrigin
    ? [resolvedOrigin, ...candidates.filter((origin) => origin !== resolvedOrigin)]
    : candidates;
  const finalOptions = sessionId
    ? { ...options, headers: { ...options?.headers, Authorization: `Bearer ${sessionId}` } }
    : options;

  let lastError;
  for (const origin of tryOrder) {
    try {
      const url = `${origin}${path}`;
      const response =
        finalOptions !== undefined ? await fetch(url, finalOptions) : await fetch(url);
      if (!response.ok) throw new Error(response.statusText);
      resolvedOrigin = origin;
      return response;
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError ?? new Error('No server origin available');
}

export async function fetchResults(sessionId) {
  const response = await request(`/gallery/${sessionId}/results`);
  return response.json();
}

export async function fetchResultsPage(sessionId, page) {
  const response = await request(`/gallery/${sessionId}/results/page/${page}`);
  return response.json();
}

export async function fetchIndexingStatus(sessionId) {
  const response = await request(`/indexing/${sessionId}/status`);
  return response.json();
}

export async function cancelIndexing(sessionId) {
  await request(`/indexing/${sessionId}/cancel`, { method: 'POST' });
}

function encodePartition(partition) {
  return partition.split('/').map(encodeURIComponent).join('/');
}

/** URL for a match's proxied AVIF thumbnail grid, or null if unavailable. */
export function thumbnailUrl(match) {
  if (!resolvedOrigin || !match?.library || !match?.avifHash || match?.tileIndex == null) return null;
  return `${resolvedOrigin}${mediaPrefix()}/thumbnail/${encodeURIComponent(match.library)}/${encodePartition(match.partition)}/${encodeURIComponent(match.avifHash)}`;
}

/** URL for a match's on-demand JPEG preview, or null if unavailable. */
export function previewUrl(match) {
  if (!resolvedOrigin || !match?.library || !match?.contentHash) return null;
  return `${resolvedOrigin}${mediaPrefix()}/previews/${encodeURIComponent(match.library)}/${encodePartition(match.partition)}/${encodeURIComponent(match.contentHash)}.jpg`;
}

/**
 * URL for range-streaming a video match's original file, or null if
 * unavailable. Structurally mirrors previewUrl(); the extension is cosmetic
 * (browsers key off Wally's Content-Type, not the URL suffix — OEC-39a §3),
 * so a `.mp4` default is fine even for MOV sources.
 */
export function videoUrl(match) {
  if (!resolvedOrigin || !match?.library || !match?.contentHash) return null;
  const ext = /\.mov$/i.test(match.filename ?? '') ? 'mov' : 'mp4';
  return `${resolvedOrigin}${mediaPrefix()}/video/${encodeURIComponent(match.library)}/${encodePartition(match.partition)}/${encodeURIComponent(match.contentHash)}.${ext}`;
}
