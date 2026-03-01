import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [svelte()],
  base: './',
  build: {
    outDir: '../src/woof/gallery/dist',
    emptyOutDir: true,
    // Single self-contained HTML file — no external chunks
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
      },
    },
  },
});
