import { defineConfig, devices } from '@playwright/test'

// End-to-end tests against the web app (dev server, or the production build in CI), which proxies
// /api to a live API on localhost:8000 and signs in against the Firebase Auth emulator on 127.0.0.1:9099.
// Start both first (see README.md); each test makes its own jobs, so the database can hold others.
const CI = !!process.env.CI

// The same local values as .env.development; the CI build does not read that file.
const firebaseEnv = {
  VITE_FIREBASE_API_KEY: 'demo-key',
  VITE_FIREBASE_AUTH_DOMAIN: 'demo-justpass.firebaseapp.com',
  VITE_FIREBASE_PROJECT_ID: 'demo-justpass',
  VITE_FIREBASE_APP_ID: 'demo-app',
  VITE_FIREBASE_AUTH_EMULATOR_URL: 'http://127.0.0.1:9099',
}

export default defineConfig({
  testDir: 'e2e',
  // The fake agent takes 10 s per round, so a test that waits for a round or two needs patience.
  // Tests make their own jobs and share no state, so they can run side by side.
  fullyParallel: true,
  workers: 4,
  timeout: 90_000,
  expect: { timeout: 25_000 },
  retries: CI ? 1 : 0,
  reporter: CI ? [['list'], ['github']] : 'list',
  use: { baseURL: 'http://localhost:5173', trace: 'on-first-retry' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  // CI tests the production build; vite preview proxies /api like the dev server.
  webServer: {
    command: CI ? 'pnpm build && pnpm preview --port 5173 --strictPort' : 'pnpm dev --port 5173 --strictPort',
    url: 'http://localhost:5173',
    env: firebaseEnv,
    reuseExistingServer: !CI,
    timeout: 120_000, // in CI this includes the production build
  },
})
