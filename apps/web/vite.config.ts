import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  // Production serves /api/** from the same origin (Firebase Hosting rewrite to Cloud Run), and
  // the dev server does the same, so the API sees identical paths in both.
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
