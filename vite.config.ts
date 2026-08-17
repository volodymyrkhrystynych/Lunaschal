/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import path from 'path';
import fs from 'fs';

// Set by start-server.sh when serving network mode over a Tailscale cert —
// iOS Safari only exposes navigator.mediaDevices on a secure context, so
// LAN/Tailscale access to the mic (voice input) requires real HTTPS.
const httpsCert = process.env.VITE_HTTPS_CERT;
const httpsKey = process.env.VITE_HTTPS_KEY;
const https =
  httpsCert && httpsKey
    ? { cert: fs.readFileSync(httpsCert), key: fs.readFileSync(httpsKey) }
    : undefined;
const tailscaleHost = process.env.TAILSCALE_HOSTNAME;

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
    // extension/ has no build step — it is loaded unpacked — so its pure
    // modules are plain .js and are imported here directly. Field-label
    // derivation is the riskiest logic in the extension and the part that
    // most needs to stay covered.
    include: ['src/**/*.{test,spec}.{ts,tsx}', 'extension/**/*.{test,spec}.js'],
    setupFiles: ['src/test/setup.ts'],
  },
  server: {
    port: 5173,
    https,
    // Without this, the HMR client (which reads window.location.hostname)
    // still tries to open its websocket to "localhost" by default, which
    // fails for browsers on other devices connecting via the Tailscale name.
    hmr: https && tailscaleHost ? { host: tailscaleHost } : undefined,
    proxy: {
      '/api': {
        // Overridden by start-node.sh so a weak machine can run the frontend
        // locally while proxying API calls to the backend on another machine.
        // Flask itself speaks HTTPS-only once a cert is wired in (start-server.sh),
        // so the default target must follow suit or every proxied call gets ECONNRESET.
        //
        // :5001 is the *dev* backend. :5000 is the production server
        // (lunaschal.service), which now runs full-time — pointing the dev proxy
        // there would silently serve the running production build while you
        // edited backend code and saw nothing change.
        target:
          process.env.VITE_API_PROXY_TARGET ||
          (https ? 'https://localhost:5001' : 'http://localhost:5001'),
        changeOrigin: true,
        // The cert's CN is the Tailscale hostname, not "localhost" — this hop
        // never leaves the machine, so skipping hostname verification is fine.
        secure: false,
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
