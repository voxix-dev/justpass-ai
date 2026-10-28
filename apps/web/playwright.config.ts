import { defineConfig, devices } from '@playwright/test'

// End-to-end tests against the dev server, which proxies /api to a live API on localhost:8000.
// Start the API first (see README.md); each test makes its own jobs, so the database can hold others.
const CI = !!process.env.CI

export default defineConfig({
  testDir: 'e2e',
  retries: CI ? 1 : 0,
  reporter: CI ? [['list'], ['github']] : 'list',
  use: { baseURL: 'http://localhost:5173', trace: 'on-first-retry' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: { command: 'pnpm dev --port 5173 --strictPort', url: 'http://localhost:5173', reuseExistingServer: !CI },
})
