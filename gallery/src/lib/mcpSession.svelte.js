/**
 * MCP Apps session bootstrap for the gallery iframe.
 *
 * Concentrates everything about the postMessage channel to the MCP host —
 * constructing the App, parsing tool results, refreshing the server origin /
 * session id from each result, applying host context (locale/theme/styles), and the
 * connect() handshake — so the root component keeps only view/selection state.
 *
 * The host channel is one of two entry paths (the other is URL params handled
 * by the caller); connect() may never resolve outside a real host, so it is
 * fired-and-forgotten here just as it was inline.
 */

import { App, applyHostStyleVariables, applyDocumentTheme } from '@modelcontextprotocol/ext-apps';
import { initServerOrigins, initSessionId } from './api.svelte.js';
import { applyLocale } from './locale.js';

function applyHostContext(ctx) {
  if (!ctx) return;
  if (ctx.locale) applyLocale(ctx.locale);
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
}

// Only include a flag when the host actually reported it, so a context change
// carrying just one field never clobbers the other's last known value.
function displayModeFlags(ctx) {
  const flags = {};
  if (ctx?.availableDisplayModes !== undefined) {
    flags.canFullscreen = ctx.availableDisplayModes.includes('fullscreen');
  }
  if (ctx?.displayMode !== undefined) {
    flags.isFullscreen = ctx.displayMode === 'fullscreen';
  }
  return flags;
}

/**
 * Construct the MCP App and wire the tool-result / host-context / connect
 * handlers. All updates are pushed to the caller through the supplied
 * callbacks; the caller owns the reactive state they mutate.
 *
 * @param {object} handlers
 * @param {(app: App) => void} handlers.onApp - receives the constructed instance
 * @param {() => void} handlers.onReady - connect() handshake completed
 * @param {(flags: {canFullscreen: boolean, isFullscreen: boolean}) => void} handlers.onDisplayMode
 * @param {(info: {sessionId: string}) => void} handlers.onIndexing
 * @param {(info: {querySummary: string, sessionId: string}) => void} handlers.onGallery
 * @returns {App | null} the app, or null if not running inside an MCP host
 */
export function initMcpSession({ onApp, onReady, onDisplayMode, onIndexing, onGallery }) {
  let app;
  try {
    app = new App({ name: 'OuEstCharlie', version: '1.0.0' });
  } catch {
    return null; // not running inside an MCP host
  }
  onApp(app);

  app.ontoolresult = ({ content }) => {
    const text = (content ?? []).find((b) => b.type === 'text')?.text;
    if (!text) return;
    const result = JSON.parse(text);
    // Refresh candidate origins from the tool result — in the MCP iframe
    // context location.origin is ui://… not the Woof HTTP server URL, and the
    // server may have restarted on a new port since the page loaded.
    initServerOrigins(result.serverUrls ?? [result.serverUrl]);

    if (result.type === 'indexing') {
      // The indexing session_id is the frontend's credential; library/partition
      // scope are read from the status endpoint, not the tool result.
      initSessionId(result.session_id);
      onIndexing({ sessionId: result.session_id });
      return;
    }

    // Gallery mode (result.type === 'gallery' or legacy without type field): the
    // merged session id authenticates and scopes /gallery/{session_id}/… requests.
    initSessionId(result.session_id);
    onGallery({ querySummary: result.querySummary, sessionId: result.session_id });
  };

  app.onhostcontextchanged = (ctx) => {
    onDisplayMode(displayModeFlags(ctx));
    applyHostContext(ctx);
  };

  app
    .connect()
    .then(() => {
      onReady();
      const ctx = app.getHostContext();
      onDisplayMode({
        canFullscreen: ctx?.availableDisplayModes?.includes('fullscreen') ?? false,
        isFullscreen: ctx?.displayMode === 'fullscreen',
      });
      applyLocale(ctx?.locale ?? navigator.language);
      applyHostContext({ theme: ctx?.theme, styles: ctx?.styles });
    })
    .catch(() => {});

  return app;
}
