import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { README_IMAGES } from './readme-images'

// `npm run capture:readme` writes the PNGs and never the GIF, which only `capture:live`
// records. Run the capture into a temporary directory that already holds a sentinel GIF
// and check the GIF is left byte for byte.
test('capture:readme writes the PNGs and leaves an existing GIF unchanged', () => {
  test.setTimeout(3 * 60 * 1000)
  const out = mkdtempSync(join(tmpdir(), 'capture-readme-')) + '/'
  try {
    const gif = out + README_IMAGES.gif
    const sentinel = Buffer.from('GIF89a sentinel: capture:readme must not touch this file')
    writeFileSync(gif, sentinel)

    // Its own ports, reporter and output directory, so it does not collide with this run.
    execFileSync(
      'npx',
      ['playwright', 'test', '-c', 'playwright.capture.config.ts', '--reporter=list', `--output=${out}results`],
      {
        env: {
          ...process.env,
          CAPTURE_OUT: out,
          E2E_PORT: '5197',
          E2E_BACKEND_PORT: '5196',
        },
        stdio: 'pipe',
      },
    )

    expect(readFileSync(gif).equals(sentinel)).toBe(true)
    for (const [key, file] of Object.entries(README_IMAGES)) {
      if (key !== 'gif') expect(statSync(out + file).size, file).toBeGreaterThan(0)
    }
  } finally {
    rmSync(out, { recursive: true, force: true })
  }
})
