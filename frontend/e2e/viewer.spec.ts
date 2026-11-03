import { expect, json, pdf, test, type ApiMock } from './fixtures'
import type { Page } from '@playwright/test'

// The fixture PDF (fixtures/make-sample-pdf.mjs) is US Letter, 612 x 792 points,
// with one line of 18 pt Helvetica per page whose baseline starts at (72, 720).
// Page 2 reads "Revenue grew twelve percent in the third quarter."
const PAGE_W = 612
const PAGE_H = 792

const collections = [
  {
    id: 1,
    name: 'default',
    documents: [
      {
        id: 'd1',
        filename: 'report.pdf',
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

async function askAndOpen(page: Page, api: ApiMock, claim: Record<string, unknown>) {
  api.on('GET', '/api/collections', json(collections))
  api.on('GET', '/api/documents/d1/pdf', pdf())
  api.on('POST', '/api/collections/default/chat', (route) =>
    route.fulfill({
      headers: { 'Content-Type': 'application/x-ndjson' },
      body:
        [
          {
            type: 'claim',
            text: 'Revenue rose.',
            document: { id: 'd1', filename: 'report.pdf' },
            page: 2,
            chunk_id: 'd1-2',
            ...claim,
          },
          { type: 'done', mode: 'fast', model: 'fake-model', cost: null },
        ]
          .map((e) => JSON.stringify(e))
          .join('\n') + '\n',
    }),
  )
  await page.goto('/')
  await page.getByLabel('Ask a question').fill('How did revenue do?')
  await page.getByRole('button', { name: 'Ask' }).click()
  await page.getByRole('button', { name: 'report.pdf, p. 2' }).click()

  const viewer = page.getByRole('dialog', { name: 'report.pdf, page 2' })
  await expect(viewer.getByText('Page 2 of 2')).toBeVisible()
  // The page itself is on screen: the canvas is drawn and the text layer is page 2's.
  const canvas = viewer.locator('canvas')
  await expect(canvas).toBeVisible()
  await expect(viewer.locator('.textLayer')).toContainText('third quarter')
  await expect(viewer.locator('.textLayer')).not.toContainText('quick brown fox')
  return { viewer, canvas }
}

/** Where `box` sits on the page, in PDF points from the page's top-left corner. */
function inPoints(
  box: { x: number; y: number; width: number; height: number },
  page: { x: number; y: number; width: number; height: number },
) {
  const sx = PAGE_W / page.width
  const sy = PAGE_H / page.height
  return {
    left: (box.x - page.x) * sx,
    right: (box.x + box.width - page.x) * sx,
    top: (box.y - page.y) * sy,
    bottom: (box.y + box.height - page.y) * sy,
  }
}

test('a citation chip opens its page with the quote highlighted', async ({ page, api }) => {
  // Case, spacing and the line break differ from the PDF: the search is normalised.
  const { viewer, canvas } = await askAndOpen(page, api, {
    quote: 'TWELVE   percent\nin the',
  })

  const marks = viewer.locator('mark')
  await expect(marks.first()).toBeVisible()
  expect((await marks.allTextContents()).join('')).toBe('twelve percent in the')
  await expect(viewer.getByText(/quote not located/i)).toHaveCount(0)

  // The highlight lands on the words: on the text line, and after "Revenue grew ".
  const at = inPoints((await marks.first().boundingBox())!, (await canvas.boundingBox())!)
  expect(at.top).toBeGreaterThan(PAGE_H - 720 - 18)
  expect(at.bottom).toBeLessThan(PAGE_H - 720 + 8)
  expect(at.left).toBeGreaterThan(72 + 80)
  expect(at.right).toBeLessThan(PAGE_W - 72)
  const last = inPoints((await marks.last().boundingBox())!, (await canvas.boundingBox())!)
  expect(last.right - at.left).toBeGreaterThan(120)
  expect(last.right - at.left).toBeLessThan(260)

  // Nothing is drawn over it: the highlight is what is at its own centre.
  const box = (await marks.first().boundingBox())!
  const hit = await page.evaluate(
    ([x, y]) => document.elementFromPoint(x, y)?.tagName,
    [box.x + box.width / 2, box.y + box.height / 2],
  )
  expect(hit).toBe('MARK')
  await expect(viewer.locator('.quote-box')).toHaveCount(0)
})

test('a quote that is not on the page opens the page with a notice and the quote', async ({
  page,
  api,
}) => {
  const quote = 'profits fell sharply in <b>May</b>'
  const { viewer } = await askAndOpen(page, api, { quote })

  const notice = viewer.getByRole('status')
  await expect(notice).toContainText(/quote not located/i)
  await expect(viewer.getByRole('blockquote')).toHaveText(quote)
  await expect(viewer.locator('mark')).toHaveCount(0)
  await expect(viewer.locator('.quote-box')).toHaveCount(0)
})

test('bounding boxes from the API are drawn where they say, without a text search', async ({
  page,
  api,
}) => {
  // The quote is not on the page, so only the box can place the highlight.
  const { viewer, canvas } = await askAndOpen(page, api, {
    quote: 'not in the text layer',
    boxes: [{ x0: 300, y0: 700, x1: 500, y1: 740 }],
  })

  const box = viewer.locator('.quote-box')
  await expect(box).toHaveCount(1)
  const at = inPoints((await box.boundingBox())!, (await canvas.boundingBox())!)
  expect(at.left).toBeCloseTo(300, -1)
  expect(at.right).toBeCloseTo(500, -1)
  expect(at.top).toBeCloseTo(PAGE_H - 740, -1)
  expect(at.bottom).toBeCloseTo(PAGE_H - 700, -1)
  await expect(viewer.locator('mark')).toHaveCount(0)
  await expect(viewer.getByText(/quote not located/i)).toHaveCount(0)
})

test('the viewer closes, and a PDF that fails to load says so', async ({ page, api }) => {
  const { viewer } = await askAndOpen(page, api, { quote: 'twelve percent' })
  await viewer.getByRole('button', { name: 'Close' }).click()
  await expect(viewer).toHaveCount(0)

  api.on('GET', '/api/documents/d1/pdf', json({ detail: 'file not found' }, 404))
  await page.getByRole('button', { name: 'report.pdf, p. 2' }).click()
  await expect(
    page.getByRole('dialog', { name: 'report.pdf, page 2' }).getByRole('alert'),
  ).toContainText(/could not open/i)
})

test('a citation to a page the PDF does not have says so', async ({ page, api }) => {
  api.on('GET', '/api/collections', json(collections))
  api.on('GET', '/api/documents/d1/pdf', pdf())
  api.on('POST', '/api/collections/default/chat', (route) =>
    route.fulfill({
      headers: { 'Content-Type': 'application/x-ndjson' },
      body:
        JSON.stringify({
          type: 'claim',
          text: 'Somewhere.',
          document: { id: 'd1', filename: 'report.pdf' },
          page: 5,
          chunk_id: 'd1-5',
          quote: 'twelve percent',
        }) + '\n',
    }),
  )
  await page.goto('/')
  await page.getByLabel('Ask a question').fill('Where?')
  await page.getByRole('button', { name: 'Ask' }).click()
  await page.getByRole('button', { name: 'report.pdf, p. 5' }).click()

  await expect(
    page.getByRole('dialog', { name: 'report.pdf, page 5' }).getByRole('alert'),
  ).toHaveText('Could not open report.pdf: it has no page 5')
})
