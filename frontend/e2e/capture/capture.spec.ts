import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { json, SAMPLE_PDF_PATH, test } from '../fixtures'
import { README_IMAGES } from '../readme-images'

// CAPTURE_OUT (a directory ending in `/`) redirects the images; e2e/capture-readme.spec.ts
// runs this capture into a temporary directory with it.
const OUT = process.env.CAPTURE_OUT ?? fileURLToPath(new URL('../../../docs/img/', import.meta.url))

const ready = (id: string, filename: string, pages: number) => ({
  id,
  filename,
  pages,
  size_bytes: 48_000,
  status: {
    state: 'ready',
    error: null,
    pages_total: pages,
    pages_read: pages,
    pages_failed: 0,
    failures: [],
  },
})

const claim = (text: string, id: string, filename: string, page: number, quote: string) => ({
  type: 'claim',
  text,
  document: { id, filename },
  page,
  chunk_id: `${id}-${page}`,
  quote,
})

const ndjson = (events: unknown[]) => events.map((e) => JSON.stringify(e)).join('\n') + '\n'

// The README's GIF is a real session, recorded by `npm run capture:live` (e2e/live/);
// this mocked page writes the screenshots only.
test('captures the README screenshots', async ({ page, api }) => {
  mkdirSync(OUT, { recursive: true })
  let documents: ReturnType<typeof ready>[] = []
  api.on('GET', '/api/collections', (route) => json([{ id: 1, name: 'default', documents }])(route))
  api.on('POST', '/api/collections/default/documents', async (route) => {
    documents = [ready('d1', 'lease.pdf', 4)]
    await route.fulfill({ status: 201, json: { id: 'd1' } })
  })
  api.on('POST', '/api/collections/default/chat', (route) =>
    route.fulfill({
      headers: { 'Content-Type': 'application/x-ndjson' },
      body: ndjson([
        claim('The lease runs for two years.', 'd1', 'lease.pdf', 3, 'a term of two (2) years'),
        claim('It can be renewed once.', 'd1', 'lease.pdf', 4, 'one renewal of equal length'),
        { type: 'done', mode: 'fast', model: 'fake-model', cost: null },
      ]),
    }),
  )

  await page.goto('/')
  await page.getByRole('heading', { name: 'No documents yet' }).waitFor()

  await page.locator('input[type=file]').setInputFiles(SAMPLE_PDF_PATH)
  await page.getByText('Ready: 4 pages').waitFor()
  await page.screenshot({ path: OUT + README_IMAGES.upload })

  await page.getByLabel('Ask a question').fill('How long is the lease?')
  await page.getByRole('button', { name: 'Ask' }).click()
  const answer = page.getByRole('article', { name: 'Answer' })
  await page.getByRole('article', { name: 'Answer' }).getByRole('listitem').nth(1).waitFor()
  await page.waitForFunction(() => document.querySelector('[aria-busy="false"]'))
  await page.screenshot({ path: OUT + README_IMAGES.answer })

  // Dark and phone shots come from the clean answered state, before the viewer opens.
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.screenshot({ path: OUT + README_IMAGES.dark })
  await page.emulateMedia({ colorScheme: 'light' })
  await page.setViewportSize({ width: 390, height: 780 })
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: OUT + README_IMAGES.phone })
  // Back to the desktop size for the citation close-up.
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.evaluate(() => window.scrollTo(0, 0))

  const chip = answer.getByRole('note', { name: 'Citation' }).first()
  await chip.click()
  await chip.hover()
  // The chip's quote is a native tooltip, which screenshots do not capture, so this
  // is a close-up of the cited answer.
  await answer.screenshot({ path: OUT + README_IMAGES.citation })
})
