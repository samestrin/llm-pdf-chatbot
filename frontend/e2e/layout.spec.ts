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

// The documents panel is the page's sidebar; the chat is a region of the main landmark.
const box = async (page: Page, name: 'Documents' | 'Chat') =>
  (await page
    .getByRole(name === 'Documents' ? 'complementary' : 'region', { name })
    .boundingBox())!

for (const scheme of ['light', 'dark'] as const) {
  for (const width of WIDTHS) {
    test(`at ${width} px the ${scheme} theme has no horizontal scroll`, async ({ page, api }) => {
      withContent(api)
      await page.setViewportSize({ width, height: HEIGHT })
      await page.emulateMedia({ colorScheme: scheme })
      await page.goto('/')
      await expect(page.getByRole('banner')).not.toContainText('Backend')
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

test('at 1600 px the header bar and the content are 1216 px wide and the bar is 56 px tall', async ({
  page,
  api,
}) => {
  withContent(api)
  await page.setViewportSize({ width: 1600, height: HEIGHT })
  await page.goto('/')
  await expect(page.getByRole('listitem', { name: LONG_NAME })).toBeVisible()

  // The shell measures the bar's inner row and main's inner container (docs/demo-shell.md).
  const row = (await page.locator('header .app-bar').boundingBox())!
  const content = (await page.locator('main > :first-child').boundingBox())!
  expect(row.width).toBe(1216)
  expect(row.height).toBe(56)
  expect(content.width).toBe(1216)
  expect(row.x).toBe(content.x)
  // Logo, name and nav slot are in the bar; the page title is not.
  const banner = page.getByRole('banner')
  await expect(banner.getByRole('link', { name: 'PDF Chat' })).toBeVisible()
  await expect(banner.getByRole('navigation', { name: 'Main' })).toBeVisible()
  await expect(banner.getByRole('heading')).toHaveCount(0)
  await expect(page.getByRole('main').getByRole('heading', { level: 1 })).toBeVisible()
})

// The shell footer (docs/demo-shell.md): copyright and licence left, the links
// row right from 48rem, a centred column below it, on every page.
for (const width of [375, 1600] as const) {
  test(`at ${width} px the footer shows the copyright line and the links row`, async ({
    page,
    api,
  }) => {
    withContent(api)
    await page.setViewportSize({ width, height: HEIGHT })
    await page.goto('/')
    await expect(page.getByRole('listitem', { name: LONG_NAME })).toBeVisible()

    const footer = page.getByRole('contentinfo')
    await expect(footer).toContainText(
      `© 2025–${new Date().getFullYear()} Sam Estrin · MIT License`,
    )
    const nav = footer.getByRole('navigation', { name: 'Footer' })
    // Only pages the app already serves: the API reference, no Privacy or Terms.
    await expect(nav.getByRole('link')).toHaveCount(1)
    await expect(nav.getByRole('link', { name: 'API' })).toHaveAttribute('href', '/api/docs')
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0)

    const copy = (await footer.locator('.footer-copy').boundingBox())!
    const links = (await nav.boundingBox())!
    if (width === 1600) {
      // The inner row is --page-max wide like the bar and main, padding inside.
      const row = (await footer.locator('.app-footer-row').boundingBox())!
      const content = (await page.locator('main > :first-child').boundingBox())!
      expect(row.width).toBe(1216)
      expect(row.x).toBe(content.x)
      // Copyright left, links right, on one line.
      expect(copy.x).toBeLessThan(links.x)
      expect(
        Math.abs(copy.y + copy.height / 2 - (links.y + links.height / 2)),
      ).toBeLessThanOrEqual(1)
    } else {
      // A centred column: the copyright above the links, both centred.
      expect(copy.y + copy.height).toBeLessThanOrEqual(links.y)
      expect(Math.abs(copy.x + copy.width / 2 - width / 2)).toBeLessThanOrEqual(1)
      expect(Math.abs(links.x + links.width / 2 - width / 2)).toBeLessThanOrEqual(1)
    }
  })
}

test('on a short page the footer sits at the bottom of the viewport', async ({ page }) => {
  // The default fixtures serve no collections, so the page is short.
  await page.setViewportSize({ width: 1600, height: HEIGHT })
  await page.goto('/')
  await expect(page.getByRole('contentinfo')).toBeVisible()
  const footer = (await page.getByRole('contentinfo').boundingBox())!
  expect(Math.abs(footer.y + footer.height - HEIGHT)).toBeLessThanOrEqual(1)
})

test('the header bar stays at the top while the page scrolls', async ({ page, api }) => {
  withContent(api)
  await page.setViewportSize({ width: 1280, height: 400 })
  await page.goto('/')
  await ask(page, 'How did revenue do?')
  await page.mouse.wheel(0, 2000)
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(0)

  const bar = (await page.getByRole('banner').boundingBox())!
  expect(bar.y).toBe(0)
  await expect(page.getByRole('banner').getByRole('link', { name: 'PDF Chat' })).toBeInViewport()
})

test('the focused skip link is painted above the sticky header bar', async ({ page, api }) => {
  withContent(api)
  await page.setViewportSize({ width: 1280, height: HEIGHT })
  await page.goto('/')
  await expect(page.getByRole('listitem', { name: LONG_NAME })).toBeVisible()
  await page.keyboard.press('Tab')
  const skip = page.getByRole('link', { name: 'Skip to chat' })
  await expect(skip).toBeFocused()

  // The element drawn at the link's centre is the link, not the bar over it.
  const onTop = await skip.evaluate((link) => {
    const r = link.getBoundingClientRect()
    const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2)
    return hit !== null && link.contains(hit)
  })
  expect(onTop).toBe(true)
})

test('below 48rem the nav slot is hidden and the bar does not scroll sideways', async ({
  page,
  api,
}) => {
  withContent(api)
  await page.setViewportSize({ width: 360, height: HEIGHT })
  await page.goto('/')
  await expect(page.getByRole('banner').getByRole('link', { name: 'PDF Chat' })).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Main' })).toBeHidden()
  expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0)
})

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
