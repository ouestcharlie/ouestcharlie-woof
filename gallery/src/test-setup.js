import '@testing-library/jest-dom/vitest';

// jsdom does not implement ResizeObserver (used by Svelte's bind:clientWidth).
// Stub it so components that use dimension bindings render without throwing.
// clientWidth stays 0, which is fine — tests verify logical behaviour, not layout.
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};
