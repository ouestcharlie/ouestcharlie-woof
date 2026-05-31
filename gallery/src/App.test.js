import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import App from './App.svelte';

// Stub the MCP Apps SDK — not available in jsdom.
vi.mock('@modelcontextprotocol/ext-apps', () => ({
  App: class {
    ontoolresult = null;
    onhostcontextchanged = null;
    connect() { return new Promise(() => {}); } // never resolves outside host
    getHostContext() { return {}; }
  },
  applyHostStyleVariables: () => {},
  applyDocumentTheme: () => {},
}));

// Helpers ---------------------------------------------------------------

function makeSession(overrides = {}) {
  return {
    matches: [],
    querySummary: 'test query',
    totalCount: 0,
    pageSize: 500,
    ...overrides,
  };
}

function makeMatch(i) {
  return { contentHash: `hash${i}`, filename: `IMG_${String(i).padStart(3, '0')}.jpg`, partition: 'p' };
}

function makeMatches(n) {
  return Array.from({ length: n }, (_, i) => makeMatch(i));
}

// Set ?token= in the URL so App uses the HTTP path (not MCP postMessage).
function setUrlToken(token) {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: {
      origin: 'http://localhost',
      search: `?token=${token}`,
    },
  });
}

// jsdom displayPageSize: columns=1 (clientWidth=0), ROWS=3 → 3 tiles per local page.
const JSDOM_PAGE_SIZE = 3;

// -----------------------------------------------------------------------

describe('App — initial session load via URL token', () => {
  beforeEach(() => setUrlToken('tok1'));
  afterEach(() => vi.restoreAllMocks());

  it('renders photo count from totalCount after session loads', async () => {
    const session = makeSession({ matches: makeMatches(3), totalCount: 600 });
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(session) });

    const { getByText } = render(App);
    await waitFor(() => expect(getByText('600 photos')).toBeTruthy());
    expect(global.fetch).toHaveBeenCalledWith('http://localhost/api/results/tok1');
  });

  it('shows error message when session fetch fails', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, statusText: 'Not Found' });

    const { getByText } = render(App);
    await waitFor(() => expect(getByText(/Error/)).toBeTruthy());
  });

  it('shows default title before querySummary is provided via MCP', async () => {
    // The URL-token path does not supply querySummary; the header falls back to 'OuEstCharlie'.
    const session = makeSession({ matches: makeMatches(1), totalCount: 1 });
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(session) });

    const { getByText } = render(App);
    await waitFor(() => expect(getByText('OuEstCharlie')).toBeTruthy());
  });
});

// -----------------------------------------------------------------------

describe('App — server page navigation', () => {
  beforeEach(() => setUrlToken('tok2'));
  afterEach(() => vi.restoreAllMocks());

  it('fetches next server page when Next is clicked at last local page', async () => {
    // Session has 3 matches (1 display page) but totalCount=600 → more server pages.
    const session = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), totalCount: 600 });
    const page1 = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), totalCount: 600 });

    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(session) }) // initial
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(page1) });   // page/1

    const { getAllByText } = render(App);
    // Wait for the gallery to finish loading.
    await waitFor(() => expect(getAllByText(/Next/).length).toBeGreaterThan(0));

    const nextBtn = getAllByText(/Next/)[0].closest('button');
    await fireEvent.click(nextBtn);

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('http://localhost/api/results/tok2/page/1'),
    );
  });

  it('fetches previous server page after navigating forward to server page 1', async () => {
    // Start on server page 0 with 3 matches and totalCount=600 → more server pages exist.
    const session = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), totalCount: 600 });
    const page1 = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), totalCount: 600 });
    const page0 = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), totalCount: 600 });

    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(session) }) // initial
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(page1) })   // /page/1
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(page0) });  // /page/0

    const { getAllByText } = render(App);
    await waitFor(() => expect(getAllByText(/Next/).length).toBeGreaterThan(0));

    // Navigate forward to server page 1.
    await fireEvent.click(getAllByText(/Next/)[0].closest('button'));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('http://localhost/api/results/tok2/page/1'),
    );

    // Now on server page 1, localPage 0 — Previous should fetch server page 0.
    await fireEvent.click(getAllByText(/Previous/)[0].closest('button'));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('http://localhost/api/results/tok2/page/0'),
    );
  });

  it('does not fetch a server page when navigating within the same server page', async () => {
    // 7 matches (3 local pages) on a single server page of totalCount=7.
    const session = makeSession({ matches: makeMatches(7), totalCount: 7 });
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(session) });

    const { getAllByText } = render(App);
    await waitFor(() => expect(getAllByText(/Next/).length).toBeGreaterThan(0));

    const nextBtn = getAllByText(/Next/)[0].closest('button');
    await fireEvent.click(nextBtn);

    // Only the initial session fetch should have been made — no /page/ call.
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('shows error in status bar when server page fetch fails', async () => {
    const session = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), totalCount: 600 });
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(session) })
      .mockResolvedValueOnce({ ok: false, statusText: 'Internal Server Error' });

    const { getAllByText, getByText } = render(App);
    await waitFor(() => expect(getAllByText(/Next/).length).toBeGreaterThan(0));

    await fireEvent.click(getAllByText(/Next/)[0].closest('button'));
    await waitFor(() => expect(getByText(/Error loading page/)).toBeTruthy());
  });
});
