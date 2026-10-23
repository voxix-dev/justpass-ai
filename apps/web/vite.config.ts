import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  // Production serves /api/** from the same origin (Firebase Hosting rewrite to Cloud Run).
  // The API serves routes without the /api prefix for now; strip it in dev.
  server: {
    proxy: { '/api': { target: 'http://localhost:8000', rewrite: (p) => p.replace(/^\/api/, '') } },
  },
})
