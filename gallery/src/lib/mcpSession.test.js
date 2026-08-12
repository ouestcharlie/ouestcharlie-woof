import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Capture constructed App instances and stub the SDK helpers.
const instances = [];
let connectResult; // resolved/rejected promise the mock's connect() returns
let hostContext; // what getHostContext() returns

vi.mock('@modelcontextprotocol/ext-apps', () => ({
  App: class {
    constructor(opts) {
      this.opts = opts;
      this.ontoolresult = null;
      this.onhostcontextchanged = null;
      instances.push(this);
    }
    connect() { return connectResult; }
    getHostContext() { return hostContext; }
  },
  applyHostStyleVariables: vi.fn(),
  applyDocumentTheme: vi.fn(),
}));

// api origin/token refresh — spy only.
const initServerOrigins = vi.fn();
const initServerToken = vi.fn();
vi.mock('./api.svelte.js', () => ({
  initServerOrigins: (...a) => initServerOrigins(...a),
  initServerToken: (...a) => initServerToken(...a),
}));

const applyLocale = vi.fn();
vi.mock('./locale.js', () => ({ applyLocale: (...a) => applyLocale(...a) }));

import { initMcpSession } from './mcpSession.svelte.js';
import { applyHostStyleVariables, applyDocumentTheme } from '@modelcontextprotocol/ext-apps';

function makeHandlers() {
  return {
    onApp: vi.fn(),
    onReady: vi.fn(),
    onDisplayMode: vi.fn(),
    onIndexing: vi.fn(),
    onGallery: vi.fn(),
  };
}

// Wrap a JSON payload the way ontoolresult receives it.
function toolResult(payload) {
  return { content: [{ type: 'text', text: JSON.stringify(payload) }] };
}

beforeEach(() => {
  instances.length = 0;
  connectResult = new Promise(() => {}); // never resolves unless a test overrides
  hostContext = {};
  vi.clearAllMocks();
});

afterEach(() => vi.restoreAllMocks());

describe('initMcpSession — construction', () => {
  it('constructs an App and hands it back via onApp', () => {
    const h = makeHandlers();
    const app = initMcpSession(h);
    expect(instances).toHaveLength(1);
    expect(app).toBe(instances[0]);
    expect(h.onApp).toHaveBeenCalledWith(instances[0]);
  });
});

describe('initMcpSession — tool results', () => {
  it('routes an indexing result to onIndexing and uses session_id as the token', () => {
    const h = makeHandlers();
    initMcpSession(h);
    instances[0].ontoolresult(toolResult({
      type: 'indexing',
      session_id: 'sess-1',
      serverUrls: ['http://127.0.0.1:9000'],
    }));

    expect(h.onIndexing).toHaveBeenCalledWith({ sessionId: 'sess-1' });
    expect(h.onGallery).not.toHaveBeenCalled();
    expect(initServerOrigins).toHaveBeenCalledWith(['http://127.0.0.1:9000']);
    expect(initServerToken).toHaveBeenCalledWith('sess-1');
  });

  it('routes a gallery result to onGallery and uses token as the credential', () => {
    const h = makeHandlers();
    initMcpSession(h);
    instances[0].ontoolresult(toolResult({
      type: 'gallery', token: 'gtok', querySummary: 'cats',
    }));
    expect(h.onGallery).toHaveBeenCalledWith({ querySummary: 'cats', token: 'gtok' });
    expect(h.onIndexing).not.toHaveBeenCalled();
    expect(initServerToken).toHaveBeenCalledWith('gtok');
  });

  it('treats a legacy result with no type field as gallery', () => {
    const h = makeHandlers();
    initMcpSession(h);
    instances[0].ontoolresult(toolResult({ token: 'gtok', querySummary: 'dogs' }));
    expect(h.onGallery).toHaveBeenCalledWith({ querySummary: 'dogs', token: 'gtok' });
  });

  it('falls back to the singular serverUrl when serverUrls is absent', () => {
    const h = makeHandlers();
    initMcpSession(h);
    instances[0].ontoolresult(toolResult({ token: 't', serverUrl: 'http://localhost:8080' }));
    expect(initServerOrigins).toHaveBeenCalledWith(['http://localhost:8080']);
  });

  it('ignores a tool result with no text block', () => {
    const h = makeHandlers();
    initMcpSession(h);
    instances[0].ontoolresult({ content: [] });
    expect(h.onGallery).not.toHaveBeenCalled();
    expect(h.onIndexing).not.toHaveBeenCalled();
    expect(initServerOrigins).not.toHaveBeenCalled();
  });
});

describe('initMcpSession — host context changes', () => {
  it('updates only the reported display-mode flag (canFullscreen alone)', () => {
    const h = makeHandlers();
    initMcpSession(h);
    instances[0].onhostcontextchanged({ availableDisplayModes: ['inline', 'fullscreen'] });
    expect(h.onDisplayMode).toHaveBeenCalledWith({ canFullscreen: true });
  });

  it('updates only isFullscreen when only displayMode is reported', () => {
    const h = makeHandlers();
    initMcpSession(h);
    instances[0].onhostcontextchanged({ displayMode: 'fullscreen' });
    expect(h.onDisplayMode).toHaveBeenCalledWith({ isFullscreen: true });
  });

  it('applies locale, theme, and style variables from context', () => {
    const h = makeHandlers();
    initMcpSession(h);
    instances[0].onhostcontextchanged({
      locale: 'fr', theme: 'dark', styles: { variables: { '--x': '1' } },
    });
    expect(applyLocale).toHaveBeenCalledWith('fr');
    expect(applyDocumentTheme).toHaveBeenCalledWith('dark');
    expect(applyHostStyleVariables).toHaveBeenCalledWith({ '--x': '1' });
  });
});

describe('initMcpSession — connect handshake', () => {
  it('signals ready and pushes initial display mode + locale once connect resolves', async () => {
    hostContext = { availableDisplayModes: ['fullscreen'], displayMode: 'inline', locale: 'de' };
    connectResult = Promise.resolve();
    const h = makeHandlers();
    initMcpSession(h);
    await connectResult;
    await Promise.resolve(); // let the .then() chain flush

    expect(h.onReady).toHaveBeenCalled();
    expect(h.onDisplayMode).toHaveBeenCalledWith({ canFullscreen: true, isFullscreen: false });
    expect(applyLocale).toHaveBeenCalledWith('de');
  });

  it('swallows a rejected connect() (not running inside a host)', async () => {
    connectResult = Promise.reject(new Error('no host'));
    const h = makeHandlers();
    expect(() => initMcpSession(h)).not.toThrow();
    await connectResult.catch(() => {});
    await Promise.resolve();
    expect(h.onReady).not.toHaveBeenCalled();
  });
});
