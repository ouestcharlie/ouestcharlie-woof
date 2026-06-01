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
