import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  initServerOrigins,
  initServerToken,
  getResolvedOrigin,
  fetchResults,
  fetchResultsPage,
  fetchIndexingStatus,
  cancelIndexing,
  thumbnailUrl,
  previewUrl,
} from './api.svelte.js';

// Module state (resolvedOrigin, authToken) persists across tests — force a clean slate each time.
beforeEach(() => {
  initServerOrigins([]);
  initServerToken(null);
});
afterEach(() => vi.restoreAllMocks());

describe('api.svelte.js — origin fallback', () => {
  it('resolves to the first candidate that succeeds', async () => {
    initServerOrigins(['http://localhost:1', 'http://127.0.0.1:1']);
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true }) });

    await fetchResults('tok');

    expect(global.fetch).toHaveBeenCalledWith('http://localhost:1/api/results/tok');
    expect(getResolvedOrigin()).toBe('http://localhost:1');
  });

  it('falls back to the next candidate when the first is CSP-blocked (fetch rejects)', async () => {
    initServerOrigins(['http://localhost:1', 'http://127.0.0.1:1']);
    global.fetch = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ ok: true }) });

    await fetchResults('tok');

    expect(global.fetch).toHaveBeenNthCalledWith(1, 'http://localhost:1/api/results/tok');
    expect(global.fetch).toHaveBeenNthCalledWith(2, 'http://127.0.0.1:1/api/results/tok');
    expect(getResolvedOrigin()).toBe('http://127.0.0.1:1');
  });

  it('reuses the resolved origin on subsequent calls without retrying the first candidate', async () => {
    initServerOrigins(['http://localhost:1', 'http://127.0.0.1:1']);
    global.fetch = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ ok: true }) })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ ok: true }) });

    await fetchResults('tok'); // resolves to 127.0.0.1 after one fallback
    global.fetch.mockClear();

    await fetchResultsPage('tok', 1);

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(global.fetch).toHaveBeenCalledWith('http://127.0.0.1:1/api/results/tok/page/1');
  });

  it('throws once every candidate has failed', async () => {
    initServerOrigins(['http://localhost:1', 'http://127.0.0.1:1']);
    global.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));

    await expect(fetchResults('tok')).rejects.toThrow('Failed to fetch');
  });

  it('sends cancelIndexing as a POST to the resolved origin', async () => {
    initServerOrigins(['http://localhost:1']);
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });

    await cancelIndexing('sess');

    expect(global.fetch).toHaveBeenCalledWith('http://localhost:1/api/indexing/sess/cancel', {
      method: 'POST',
    });
  });

  it('fetches indexing status from the resolved origin', async () => {
    initServerOrigins(['http://localhost:1']);
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ status: 'running' }) });

    const data = await fetchIndexingStatus('sess');

    expect(global.fetch).toHaveBeenCalledWith('http://localhost:1/api/indexing/sess');
    expect(data).toEqual({ status: 'running' });
  });
});

describe('api.svelte.js — bearer token (HTTP mode)', () => {
  it('attaches an Authorization header to GET requests once a token is set', async () => {
    initServerOrigins(['http://localhost:1']);
    initServerToken('secret');
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });

    await fetchResults('tok');

    expect(global.fetch).toHaveBeenCalledWith('http://localhost:1/api/results/tok', {
      headers: { Authorization: 'Bearer secret' },
    });
  });

  it('merges the Authorization header into existing request options (e.g. POST)', async () => {
    initServerOrigins(['http://localhost:1']);
    initServerToken('secret');
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });

    await cancelIndexing('sess');

    expect(global.fetch).toHaveBeenCalledWith('http://localhost:1/api/indexing/sess/cancel', {
      method: 'POST',
      headers: { Authorization: 'Bearer secret' },
    });
  });

  it('sends no Authorization header when no token is set', async () => {
    initServerOrigins(['http://localhost:1']);
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });

    await fetchResults('tok');

    expect(global.fetch).toHaveBeenCalledWith('http://localhost:1/api/results/tok');
  });
});

describe('api.svelte.js — URL builders', () => {
  const match = { library: 'lib', partition: '2024/07', avifHash: 'avif1', tileIndex: 3, contentHash: 'hash1' };

  it('builds a thumbnail URL from the resolved origin', () => {
    initServerOrigins(['http://localhost:1']);
    expect(thumbnailUrl(match)).toBe('http://localhost:1/thumbnail/lib/2024/07/avif1');
  });

  it('builds a preview URL from the resolved origin', () => {
    initServerOrigins(['http://localhost:1']);
    expect(previewUrl(match)).toBe('http://localhost:1/previews/lib/2024/07/hash1.jpg');
  });

  it('returns null for thumbnailUrl when no origin is resolved', () => {
    initServerOrigins([]);
    expect(thumbnailUrl(match)).toBeNull();
  });

  it('returns null for previewUrl when required match fields are missing', () => {
    initServerOrigins(['http://localhost:1']);
    expect(previewUrl({ ...match, contentHash: undefined })).toBeNull();
  });

  it('scopes media under /media/{sessionToken}/ when a token is set (OEC-50b)', () => {
    initServerOrigins(['http://localhost:1']);
    initServerToken('secret');
    expect(thumbnailUrl(match)).toBe('http://localhost:1/media/secret/thumbnail/lib/2024/07/avif1');
    expect(previewUrl(match)).toBe('http://localhost:1/media/secret/previews/lib/2024/07/hash1.jpg');
  });
});
