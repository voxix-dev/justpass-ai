import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  // Production serves /api/** from the same origin (Firebase Hosting rewrite to Cloud Run), and
  // the dev server does the same, so the API sees identical paths in both. API_PORT matches the
  // compose api service's setting; `vite preview` uses the same proxy.
  server: { proxy: { '/api': `http://localhost:${process.env.API_PORT ?? 8000}` } },
})
