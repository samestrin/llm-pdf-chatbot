import { defineConfig, devices } from '@playwright/test'
import base from './playwright.config'

// Regenerates the README images (`npm run capture:readme`). Same fixture-backed
// page and servers as the e2e suite; only the spec differs.
export default defineConfig({
  ...base,
  testDir: 'e2e/capture',
  testIgnore: [],
  retries: 0,
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } }],
})
