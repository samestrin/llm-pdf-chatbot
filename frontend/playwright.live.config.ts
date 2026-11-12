import { defineConfig, devices } from '@playwright/test'

// Records the README demo GIF from a real, running instance (`npm run capture:live`).
// Unlike `capture:readme`, nothing is mocked: the page, the models and the answers are
// live. LIVE_URL is the instance's address; the collection must already be ingested.
const url = process.env.LIVE_URL
if (!url) throw new Error('LIVE_URL is required, for example LIVE_URL=http://localhost:3000')

export default defineConfig({
  testDir: 'e2e/live',
  retries: 2, // a live model can fail one stream (for example a repetition loop); try the whole recording again
  workers: 1,
  timeout: 10 * 60 * 1000,
  use: { baseURL: url },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } }],
})
