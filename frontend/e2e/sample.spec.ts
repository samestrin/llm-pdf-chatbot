import type { Page } from '@playwright/test'
import { expect, json, pdf, test, type ApiMock } from './fixtures'
import questions from '../public/samples/questions.json' with { type: 'json' }

// The public demo's read-only sample, asked with a preset that has no embedding model
// of its own: the server embeds, so it works with any key.
const PRESETS = {
  public_demo: true,
  upload_ttl_minutes: 60,
  embedding_model: 'demo-embedding',
  presets: [
    {
      id: 'synthetic',
      label: 'Synthetic',
      kind: 'key',
      chat_model: 'hf:zai-org/GLM-5.3-Flash',
      embedding_model: null,
    },
  ],
}

const SAMPLE = {
  id: 1,
  name: 'default',
  embedding_preset: 'demo',
  embedding_model: 'demo-embedding',
  read_only: true,
  documents: [
    {
      id: 's1',
      filename: 'sample.pdf',
      pages: 2,
      size_bytes: 921,
      status: {
        state: 'ready',
        error: null,
        pages_total: 2,
        pages_read: 2,
        pages_failed: 0,
        failures: [],
      },
    },
  ],
}

const SUGGESTED = questions['sample.pdf']

async function openSample(page: Page, api: ApiMock) {
  api.on('GET', '/api/presets', json(PRESETS))
  api.on('GET', '/api/collections', json([SAMPLE]))
  api.on('GET', '/api/documents/s1/pdf', pdf())
  await page.addInitScript(() =>
    sessionStorage.setItem(
      'pdfchat.model',
      // The old single-key shape on purpose: the page migrates it on load.
      JSON.stringify({ preset: 'synthetic', secret: 'sk-e2e-synthetic', remember: false }),
    ),
  )
  await page.goto('/')
}

test('a suggested question on the sample answers with a page citation that opens its page', async ({
  page,
  api,
}) => {
  let asked: unknown = null
  api.on('POST', '/api/collections/default/chat', (route) => {
    asked = route.request().postDataJSON()
    return route.fulfill({
      headers: { 'Content-Type': 'application/x-ndjson' },
      body:
        [
          {
            type: 'claim',
            text: 'Revenue grew twelve percent.',
            document: { id: 's1', filename: 'sample.pdf' },
            page: 2,
            chunk_id: '7',
            quote: 'Revenue grew twelve percent in the third quarter.',
          },
          { type: 'done', mode: 'fast', model: 'hf:zai-org/GLM-5.3-Flash', cost: null },
        ]
          .map((e) => JSON.stringify(e))
          .join('\n') + '\n',
    })
  })
  await openSample(page, api)

  const chips = page.getByRole('list', { name: 'Suggested questions' })
  await expect(chips.getByRole('button')).toHaveText(SUGGESTED)
  await chips.getByRole('button', { name: SUGGESTED[0] }).click()
  await expect.poll(() => (asked as { question?: string } | null)?.question).toBe(SUGGESTED[0])

  await page.getByRole('button', { name: 'sample.pdf, p. 2' }).click()
  const viewer = page.getByRole('dialog', { name: 'sample.pdf, page 2' })
  await expect(viewer.getByText('Page 2 of 2')).toBeVisible()
  await expect(viewer.locator('mark').first()).toBeVisible()
})

test('the sample has no Delete, and its name opens it in the viewer to page through', async ({
  page,
  api,
}) => {
  await openSample(page, api)
  const documents = page.getByRole('complementary', { name: 'Documents' })
  await expect(documents.getByRole('button', { name: 'View sample.pdf' })).toBeVisible()
  await expect(documents.getByRole('button', { name: /^Delete/ })).toHaveCount(0)

  await documents.getByRole('button', { name: 'View sample.pdf' }).click()
  const viewer = page.getByRole('dialog', { name: 'sample.pdf, page 1' })
  await expect(viewer.getByText('Page 1 of 2')).toBeVisible()
  await expect(viewer.locator('.textLayer')).toContainText('quick brown fox')
  await expect(viewer.getByRole('status')).toHaveCount(0)

  await viewer.getByRole('button', { name: 'Next page' }).click()
  const second = page.getByRole('dialog', { name: 'sample.pdf, page 2' })
  await expect(second.getByText('Page 2 of 2')).toBeVisible()
  await expect(second.locator('.textLayer')).toContainText('third quarter')
  await second.getByRole('button', { name: 'Close' }).click()
  await expect(second).toBeHidden()
  await expect(documents.getByRole('button', { name: 'View sample.pdf' })).toBeFocused()
})

test('on a phone a document opened from the list fills the screen', async ({ page, api }) => {
  await page.setViewportSize({ width: 360, height: 740 })
  await openSample(page, api)
  await page.getByRole('button', { name: 'View sample.pdf' }).click()
  const viewer = page.getByRole('dialog', { name: 'sample.pdf, page 1' })
  await expect(viewer.getByText('Page 1 of 2')).toBeVisible()
  expect(await viewer.boundingBox()).toEqual({ x: 0, y: 0, width: 360, height: 740 })
  await expect(viewer.getByRole('button', { name: 'Next page' })).toBeInViewport()
  await expect(viewer.getByRole('button', { name: 'Close' })).toBeInViewport()
})
