import { svelte } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

// Assets are served by the Woof HTTP server at /gallery-static/.
// get_gallery_html() rewrites those paths to absolute http://127.0.0.1:{port}/gallery-static/
// URLs at runtime so the MCP Apps iframe can load them.
export default defineConfig({
  plugins: [svelte()],
  base: '/gallery-static/',
  build: {
    outDir: '../src/woof/gallery/dist',
    emptyOutDir: true,
  },
});
