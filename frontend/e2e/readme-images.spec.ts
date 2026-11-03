import { statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import { MAX_IMAGE_BYTES, README_IMAGES } from './readme-images'

const dir = fileURLToPath(new URL('../../docs/img/', import.meta.url))

for (const file of Object.values(README_IMAGES)) {
  test(`docs/img/${file} exists and is at most 5 MB`, () => {
    const { size } = statSync(dir + file)
    expect(size).toBeGreaterThan(0)
    expect(size).toBeLessThanOrEqual(MAX_IMAGE_BYTES)
  })
}
