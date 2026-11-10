import { expect, json, pdf, test, type ApiMock } from './fixtures'
import type { Page } from '@playwright/test'

// The page from a 360 px phone to a desktop, in both themes.
const WIDTHS = [360, 768, 1280] as const
const HEIGHT = 800

const LONG_NAME = 'quarterly-report-with-a-very-long-file-name-that-does-not-break.pdf'

const collections = [
  {
    id: 1,
    name: 'default',
    documents: [
      {
        id: 'd1',
        filename: LONG_NAME,
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
  },
]

function withContent(api: ApiMock) {
  api.on('GET', '/api/collections', json(collections))
  api.on('GET', `/api/documents/d1/pdf`, pdf())
  api.on('POST', '/api/collections/default/chat', (route) =>
    route.fulfill({
      headers: { 'Content-Type': 'application/x-ndjson' },
      body:
        [
          {
            type: 'claim',
            text: 'Revenue grew twelve percent in the third quarter, the fastest growth this year.',
            document: { id: 'd1', filename: LONG_NAME },
            page: 2,
            chunk_id: 'd1-2',
            quote: 'Revenue grew twelve percent in the third quarter.',
          },
          { type: 'done', mode: 'fast', model: 'fake-model', cost: 0.00042 },
        ]
          .map((e) => JSON.stringify(e))
          .join('\n') + '\n',
    }),
  )
}

async function ask(page: Page, question: string) {
  await page.getByLabel('Ask a question').fill(question)
  await page.getByRole('button', { name: 'Ask' }).click()
  await expect(page.getByLabel('Answer details').last()).toBeVisible()
}

const horizontalOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)

const box = async (page: Page, name: string) =>
  (await page.getByRole('region', { name }).boundingBox())!

for (const scheme of ['light', 'dark'] as const) {
  for (const width of WIDTHS) {
    test(`at ${width} px the ${scheme} theme has no horizontal scroll`, async ({ page, api }) => {
      withContent(api)
      await page.setViewportSize({ width, height: HEIGHT })
      await page.emulateMedia({ colorScheme: scheme })
      await page.goto('/')
      await expect(page.getByRole('status').first()).toHaveText('Backend: ok')
      await expect(page.getByRole('listitem', { name: LONG_NAME })).toBeVisible()
      await ask(page, 'How did revenue do in the third quarter compared with the rest of the year?')

      expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0)
      // The theme is the one asked for, not the default.
      expect(await page.evaluate(() => getComputedStyle(document.body).colorScheme)).toContain(
        scheme,
      )
    })
  }
}

test('on a phone the documents panel stacks above the chat at full width', async ({
  page,
  api,
}) => {
  withContent(api)
  await page.setViewportSize({ width: 360, height: HEIGHT })
  await page.goto('/')
  await expect(page.getByRole('listitem', { name: LONG_NAME })).toBeVisible()

  const docs = await box(page, 'Documents')
  const chat = await box(page, 'Chat')
  expect(docs.y + docs.height).toBeLessThanOrEqual(chat.y)
  expect(Math.abs(docs.width - chat.width)).toBeLessThanOrEqual(1)
})

test('on a desktop the documents panel sits beside the chat', async ({ page, api }) => {
  withContent(api)
  await page.setViewportSize({ width: 1280, height: HEIGHT })
  await page.goto('/')
  await expect(page.getByRole('listitem', { name: LONG_NAME })).toBeVisible()

  const docs = await box(page, 'Documents')
  const chat = await box(page, 'Chat')
  expect(docs.x + docs.width).toBeLessThanOrEqual(chat.x)
  expect(Math.abs(docs.y - chat.y)).toBeLessThanOrEqual(1)
  expect(chat.width).toBeGreaterThan(docs.width)
})

test('on a phone the question box stays on screen while reading earlier answers', async ({
  page,
  api,
}) => {
  withContent(api)
  await page.setViewportSize({ width: 360, height: HEIGHT })
  await page.goto('/')
  for (let i = 1; i <= 5; i++) await ask(page, `Question number ${i} about revenue?`)

  await page.getByRole('article', { name: 'Answer' }).first().scrollIntoViewIfNeeded()
  await expect(page.getByLabel('Ask a question')).toBeInViewport()
  await expect(page.getByRole('button', { name: 'Ask' })).toBeInViewport()
})

test('on a phone the PDF viewer fills the screen', async ({ page, api }) => {
  withContent(api)
  await page.setViewportSize({ width: 360, height: HEIGHT })
  await page.goto('/')
  await ask(page, 'How did revenue do?')
  await page.getByRole('button', { name: `${LONG_NAME}, p. 2` }).click()

  const viewer = page.getByRole('dialog', { name: `${LONG_NAME}, page 2` })
  await expect(viewer.getByText('Page 2 of 2')).toBeVisible()
  expect(await viewer.boundingBox()).toEqual({ x: 0, y: 0, width: 360, height: HEIGHT })
  // The page is drawn no wider than the screen, and the close button is on it.
  const canvas = (await viewer.locator('canvas').boundingBox())!
  expect(canvas.x).toBeGreaterThanOrEqual(0)
  expect(canvas.x + canvas.width).toBeLessThanOrEqual(360)
  await expect(viewer.getByRole('button', { name: 'Close' })).toBeInViewport()
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0)

  await viewer.getByRole('button', { name: 'Close' }).click()
  await expect(viewer).toBeHidden()
})

test('on a desktop the PDF viewer stays in the chat column', async ({ page, api }) => {
  withContent(api)
  await page.setViewportSize({ width: 1280, height: HEIGHT })
  await page.goto('/')
  await ask(page, 'How did revenue do?')
  await page.getByRole('button', { name: `${LONG_NAME}, p. 2` }).click()

  const viewer = page.getByRole('dialog', { name: `${LONG_NAME}, page 2` })
  await expect(viewer.getByText('Page 2 of 2')).toBeVisible()
  const chat = await box(page, 'Chat')
  const view = (await viewer.boundingBox())!
  expect(view.x).toBeGreaterThanOrEqual(chat.x)
  expect(view.x + view.width).toBeLessThanOrEqual(chat.x + chat.width + 1)
})
