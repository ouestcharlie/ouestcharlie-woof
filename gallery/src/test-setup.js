import '@testing-library/jest-dom/vitest';

// Svelte's bind:clientWidth uses bind_element_size, which creates a ResizeObserver and
// immediately reads element.clientWidth inside an effect.
// Stub ResizeObserver so components don't throw, and mock clientWidth to return a value
// that produces 4 columns: Math.floor((652 + 4) / (160 + 4)) = Math.floor(656/164) = 4.
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
  configurable: true,
  get: () => 652,
});

// jsdom does not implement HTMLMediaElement playback methods; PreviewPanel
// calls pause() when navigating away from a video. Stub them to silence the
// "Not implemented" noise in test output.
HTMLMediaElement.prototype.play = () => Promise.resolve();
HTMLMediaElement.prototype.pause = () => {};
HTMLMediaElement.prototype.load = () => {};
