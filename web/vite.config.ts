import path from 'node:path';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  server: {
    // The page calls the API on its own origin. `changeOrigin` makes Host the API's; Origin stays the Vite page,
    // which the API lets through when it is started with OUTREACH_DEV_ORIGINS (see `[scripts.run.api]`).
    proxy: {
      '/api': { target: `http://127.0.0.1:${process.env.OUTREACH_API_PORT ?? 3100}`, changeOrigin: true },
    },
  },
});
