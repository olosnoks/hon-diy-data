import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// GitHub Pages serves the site under /hon-diy-data/; Tauri loads it from the
// app bundle at /. HONDALOG_BASE picks: scripts/release sets it per target.
const base = process.env.HONDALOG_BASE ?? '/';

export default defineConfig({
  base,
  plugins: [react()],
  clearScreen: false,
  server: { port: 5173, strictPort: true },
  build: { target: 'es2022', sourcemap: true, outDir: 'dist' },
  envPrefix: ['VITE_', 'TAURI_'],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
  },
});
