import { svelte } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';
import { paraglideVitePlugin } from '@inlang/paraglide-js';
import { defineConfig } from 'vite';

// Assets are served by the Woof HTTP server at /gallery-static/.
// get_gallery_html() rewrites those paths to absolute http://127.0.0.1:{port}/gallery-static/
// URLs at runtime so the MCP Apps iframe can load them.
export default defineConfig({
  plugins: [
    // Compile inlang messages into src/paraglide/ on dev, build, and test.
    // globalVariable strategy: the active locale is driven programmatically via
    // setLocale(tag, { reload: false }) from the MCP host context (App.svelte),
    // with no cookie/URL routing — the gallery lives in an embedded iframe.
    paraglideVitePlugin({
      project: './project.inlang',
      outdir: './src/paraglide',
      strategy: ['globalVariable', 'baseLocale'],
    }),
    svelte(),
    svelteTesting(),
  ],
  base: '/gallery-static/',
  build: {
    outDir: '../src/woof/gallery/dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.js'],
  },
});
