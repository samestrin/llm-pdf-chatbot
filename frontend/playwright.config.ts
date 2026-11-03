import { defineConfig, devices } from '@playwright/test'

// Browser tests run against the Vite dev server. The page's own `/api` calls are
// answered by route fixtures (e2e/fixtures.ts), so no real backend is needed.
// The dev server's `/api` proxy points at e2e/fake-backend.mjs, which lets a test
// check that the proxy strips `/api` and forwards to the backend.
const port = Number(process.env.E2E_PORT ?? 5199)
const backendPort = Number(process.env.E2E_BACKEND_PORT ?? 5198)

export default defineConfig({
  testDir: 'e2e',
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://localhost:${port}`,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `node e2e/fake-backend.mjs ${backendPort}`,
      url: `http://127.0.0.1:${backendPort}/health`,
      // Never reuse: a server already on the port is not ours, and the tests
      // would pass or fail against it.
      reuseExistingServer: false,
    },
    {
      command: `npx vite --port ${port} --strictPort`,
      url: `http://localhost:${port}`,
      env: { API_PROXY_TARGET: `http://127.0.0.1:${backendPort}` },
      reuseExistingServer: false,
    },
  ],
})
