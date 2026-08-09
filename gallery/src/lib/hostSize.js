// Single policy for telling the MCP host how tall this iframe should be.
//
// html/body are fixed-height with overflow hidden, so the SDK's autoResize
// (a ResizeObserver on body) never fires for content-driven changes — every
// view must report its own height explicitly. Two shapes are needed:
//   - a known fixed height (gallery/indexing inline modes), and
//   - a measured height from a DOM element (progress view, which grows as
//     status rows and summaries appear).
// Both funnel through sendSizeChanged with the same best-effort error handling.

/**
 * Report a fixed pixel height to the host. No-op without an app.
 * @param {{sendSizeChanged: (arg: {height: number}) => Promise<unknown>} | null} mcpApp
 * @param {number} height
 */
export function notifyHostHeight(mcpApp, height) {
  if (!mcpApp || !(height > 0)) return;
  mcpApp.sendSizeChanged({ height }).catch(() => {});
}

/**
 * Measure `el.scrollHeight` and report it to the host. Deferred to the next
 * animation frame so Svelte can flush pending DOM updates before measuring.
 * No-op without an app or element.
 * @param {{sendSizeChanged: (arg: {height: number}) => Promise<unknown>} | null} mcpApp
 * @param {HTMLElement | null} el
 */
export function notifyHostMeasured(mcpApp, el) {
  if (!mcpApp || !el) return;
  requestAnimationFrame(() => {
    if (!el) return;
    notifyHostHeight(mcpApp, el.scrollHeight);
  });
}
