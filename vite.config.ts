/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    // 'node' is enough for pure logic. Switch to 'jsdom' (and add the
    // jsdom + @testing-library/react deps) when adding component tests.
    environment: 'node',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    setupFiles: ['src/test/setup.ts'],
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // Overridden by start-node.sh so a weak machine can run the frontend
        // locally while proxying API calls to the backend on another machine.
        target: process.env.VITE_API_PROXY_TARGET || 'http://localhost:5000',
        changeOrigin: true,
      },
    },
    watch: {
      // SQLite runs in WAL mode, so data/*.db-wal and -shm are rewritten on
      // every DB transaction. Vite's watcher only excludes node_modules/.git
      // by default, so without this it re-processes a change event on
      // practically every API request — steadily leaking memory until the
      // dev server OOMs (observed crashing ~100s in).
      ignored: ['**/data/**', '**/data_backup/**', '**/.venv/**'],
    },
  },
  build: {
    outDir: 'dist',
  },
});
