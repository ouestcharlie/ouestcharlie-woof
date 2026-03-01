import { svelte } from '@sveltejs/vite-plugin-svelte';
import { viteSingleFile } from 'vite-plugin-singlefile';
import { defineConfig } from 'vite';

// viteSingleFile inlines all JS and CSS into index.html so that the gallery
// can be served as a single MCP App resource with no external asset requests.
export default defineConfig({
  plugins: [svelte(), viteSingleFile()],
  base: './',
  build: {
    outDir: '../src/woof/gallery/dist',
    emptyOutDir: true,
  },
});
