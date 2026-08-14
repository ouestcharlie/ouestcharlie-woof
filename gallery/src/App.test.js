import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import App from './App.svelte';

// Stub the MCP Apps SDK — not available in jsdom. Instances are captured and
// connect() resolves so tests can drive the tool-result / host-context paths
// (which flow through the real lib/mcpSession into App's callbacks).
const { mcpInstances, mcpState } = vi.hoisted(() => ({
  mcpInstances: [],
  mcpState: { hostContext: {} },
}));

vi.mock('@modelcontextprotocol/ext-apps', () => ({
  App: class {
    constructor() {
      this.ontoolresult = null;
      this.onhostcontextchanged = null;
      this.sendSizeChanged = vi.fn().mockResolvedValue(undefined);
      this.requestDisplayMode = vi.fn().mockResolvedValue(undefined);
      mcpInstances.push(this);
    }
    connect() { return Promise.resolve(); }
    getHostContext() { return mcpState.hostContext; }
  },
  applyHostStyleVariables: () => {},
  applyDocumentTheme: () => {},
}));

/** The App instance constructed by the most recent render. */
function lastMcp() { return mcpInstances[mcpInstances.length - 1]; }

/** Deliver a tool result the way the MCP host would. */
function toolResult(payload) {
  lastMcp().ontoolresult({ content: [{ type: 'text', text: JSON.stringify(payload) }] });
}

// Helpers ---------------------------------------------------------------

function makeSession(overrides = {}) {
  const matches = overrides.matches ?? [];
  return {
    matches,
    querySummary: 'test query',
    pageMap: [{ pageSize: 500, pageCount: 1, totalCount: matches.length }],
    ...overrides,
  };
}

function makeMatch(i) {
  return { contentHash: `hash${i}`, filename: `IMG_${String(i).padStart(3, '0')}.jpg`, partition: 'p' };
}

function makeMatches(n) {
  return Array.from({ length: n }, (_, i) => makeMatch(i));
}

// Put the session token in the /gallery/{token}/html path so App uses the HTTP
// path (not MCP postMessage) — the direct-browser entry.
function setUrlToken(token) {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: {
      origin: 'http://localhost',
      pathname: `/gallery/${token}/html`,
      search: '',
    },
  });
}

// No URL params → App relies on the MCP postMessage path.
function setUrlNoParams() {
  Object.defineProperty(window, 'location', {
    writable: true,
    value: { origin: 'http://localhost', pathname: '/', search: '' },
  });
}

// jsdom displayPageSize: columns=1 (clientWidth=0), ROWS=3 → 3 tiles per local page.
// test-setup.js mocks clientWidth=652 → 4 cols × 3 rows = 12 tiles per display page.
const JSDOM_PAGE_SIZE = 12;

// -----------------------------------------------------------------------

describe('App — initial session load via URL token', () => {
  beforeEach(() => setUrlToken('tok1'));
  afterEach(() => vi.restoreAllMocks());

  it('renders item count from pageMap totalCount after session loads', async () => {
    const session = makeSession({
      matches: makeMatches(3),
      pageMap: [{ pageSize: 500, pageCount: 2, totalCount: 600 }],
    });
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(session) });

    const { getByText } = render(App);
    await waitFor(() => expect(getByText('600 items')).toBeTruthy());
    expect(global.fetch).toHaveBeenCalledWith('http://localhost/gallery/tok1/results');
  });

  it('shows error message when session fetch fails', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, statusText: 'Not Found' });

    const { getByText } = render(App);
    await waitFor(() => expect(getByText(/Error/)).toBeTruthy());
  });

  it('shows default title before querySummary is provided via MCP', async () => {
    // The URL-token path does not supply querySummary; the header falls back to 'OuEstCharlie'.
    const session = makeSession({ matches: makeMatches(1) });
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
    // 1 display page loaded; pageMap says 2 server pages → Next triggers server fetch.
    const pm = [{ pageSize: 500, pageCount: 2, totalCount: 600 }];
    const session = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), pageMap: pm });
    const page1 = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), pageMap: pm });

    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(session) }) // initial
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(page1) });   // page/1

    const { getAllByText } = render(App);
    // Wait for the gallery to finish loading.
    await waitFor(() => expect(getAllByText(/Next/).length).toBeGreaterThan(0));

    const nextBtn = getAllByText(/Next/)[0].closest('button');
    await fireEvent.click(nextBtn);

    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('http://localhost/gallery/tok2/results/page/1'),
    );
  });

  it('fetches previous server page after navigating forward to server page 1', async () => {
    const pm = [{ pageSize: 500, pageCount: 2, totalCount: 600 }];
    const session = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), pageMap: pm });
    const page1 = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), pageMap: pm });
    const page0 = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), pageMap: pm });

    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(session) }) // initial
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(page1) })   // /page/1
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(page0) });  // /page/0

    const { getAllByText } = render(App);
    await waitFor(() => expect(getAllByText(/Next/).length).toBeGreaterThan(0));

    // Navigate forward to server page 1.
    await fireEvent.click(getAllByText(/Next/)[0].closest('button'));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('http://localhost/gallery/tok2/results/page/1'),
    );

    // Now on server page 1, localPage 0 — Previous should fetch server page 0.
    await fireEvent.click(getAllByText(/Previous/)[0].closest('button'));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith('http://localhost/gallery/tok2/results/page/0'),
    );
  });

  it('does not fetch a server page when navigating within the same server page', async () => {
    // 25 matches = 3 local pages (ceil(25/12)), all within one server page.
    const session = makeSession({ matches: makeMatches(25) }); // default pageMap: 1 server page
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(session) });

    const { getAllByText } = render(App);
    await waitFor(() => expect(getAllByText(/Next/).length).toBeGreaterThan(0));

    const nextBtn = getAllByText(/Next/)[0].closest('button');
    await fireEvent.click(nextBtn);

    // Only the initial session fetch should have been made — no /page/ call.
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('shows error in status bar when server page fetch fails', async () => {
    const pm = [{ pageSize: 500, pageCount: 2, totalCount: 600 }];
    const session = makeSession({ matches: makeMatches(JSDOM_PAGE_SIZE), pageMap: pm });
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(session) })
      .mockResolvedValueOnce({ ok: false, statusText: 'Internal Server Error' });

    const { getAllByText, getByText } = render(App);
    await waitFor(() => expect(getAllByText(/Next/).length).toBeGreaterThan(0));

    await fireEvent.click(getAllByText(/Next/)[0].closest('button'));
    await waitFor(() => expect(getByText(/Error loading page/)).toBeTruthy());
  });
});

// -----------------------------------------------------------------------

describe('App — MCP tool-result path', () => {
  beforeEach(() => {
    setUrlNoParams();
    mcpInstances.length = 0;
    mcpState.hostContext = {};
  });
  afterEach(() => vi.restoreAllMocks());

  it('renders the indexing view when an indexing tool result arrives', async () => {
    // IndexingProgress polls status on mount; library/partition_scope now come
    // from the status response, not the tool result.
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: 'running', progress: 0, total: 1, library_name: 'Vacations' }),
    });

    const { getByText } = render(App);
    toolResult({
      type: 'indexing', session_id: 's1', serverUrls: ['http://localhost'],
    });

    await waitFor(() => expect(getByText(/Vacations/)).toBeTruthy());
  });

  it('loads and renders a gallery when a gallery tool result arrives', async () => {
    const session = makeSession({ matches: makeMatches(3), querySummary: 'sunsets' });
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(session) });

    const { getByText } = render(App);
    toolResult({
      type: 'gallery', session_id: 'gtok', querySummary: 'sunsets',
      serverUrls: ['http://localhost'],
    });

    await waitFor(() => expect(getByText('sunsets')).toBeTruthy());
    await waitFor(() => expect(getByText('3 items')).toBeTruthy());
    expect(global.fetch).toHaveBeenCalledWith('http://localhost/gallery/gtok/results');
  });

  it('surfaces a gallery load failure in the status bar', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, statusText: 'Forbidden' });

    const { getByText } = render(App);
    toolResult({ type: 'gallery', token: 'gtok', querySummary: 'x', serverUrls: ['http://localhost'] });

    await waitFor(() => expect(getByText(/Error loading gallery/)).toBeTruthy());
  });
});

// -----------------------------------------------------------------------

describe('App — fullscreen control', () => {
  beforeEach(() => {
    setUrlToken('fstok');
    mcpInstances.length = 0;
    mcpState.hostContext = {};
  });
  afterEach(() => vi.restoreAllMocks());

  it('shows a fullscreen button and requests fullscreen when the host allows it', async () => {
    // connect() reads this on resolve → canFullscreen true, isFullscreen false.
    mcpState.hostContext = { availableDisplayModes: ['inline', 'fullscreen'], displayMode: 'inline' };
    const session = makeSession({ matches: makeMatches(1) });
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(session) });

    const { getByTitle } = render(App);
    const btn = await waitFor(() => getByTitle('Full screen'));
    await fireEvent.click(btn);

    expect(lastMcp().requestDisplayMode).toHaveBeenCalledWith({ mode: 'fullscreen' });
  });

  it('exits fullscreen on Escape when already fullscreen', async () => {
    mcpState.hostContext = { availableDisplayModes: ['inline', 'fullscreen'], displayMode: 'fullscreen' };
    const session = makeSession({ matches: makeMatches(1) });
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(session) });

    const { getByText } = render(App);
    await waitFor(() => expect(getByText('1 item')).toBeTruthy());

    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(lastMcp().requestDisplayMode).toHaveBeenCalledWith({ mode: 'inline' });
  });
});
