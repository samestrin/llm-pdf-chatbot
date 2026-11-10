import AxeBuilder from '@axe-core/playwright'
import type { Locator, Page, Route } from '@playwright/test'
import { expect, json, pdf, SAMPLE_PDF_PATH, test } from './fixtures'

const ready = {
  state: 'ready',
  error: null,
  pages_total: 2,
  pages_read: 2,
  pages_failed: 0,
  failures: [],
}
const report = { id: 'd1', filename: 'report.pdf', pages: 2, size_bytes: 921, status: ready }

const answer = (route: Route) =>
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
          quote: 'twelve percent',
        },
        { type: 'done', mode: 'fast', model: 'fake-model', cost: null },
      ]
        .map((e) => JSON.stringify(e))
        .join('\n') + '\n',
  })

/** Serious and critical axe violations on the page as it is now, one line each. */
async function seriousViolations(page: Page) {
  const { violations } = await new AxeBuilder({ page }).analyze()
  return violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
}

async function askAndOpen(page: Page) {
  await page.getByLabel('Ask a question').fill('How did revenue do?')
  await page.getByRole('button', { name: 'Ask' }).click()
  await page.getByRole('button', { name: 'report.pdf, p. 2' }).click()
  const viewer = page.getByRole('dialog', { name: 'report.pdf, page 2' })
  await expect(viewer.getByText('Page 2 of 2')).toBeVisible()
  await expect(viewer.locator('mark').first()).toBeVisible()
}

for (const scheme of ['light', 'dark'] as const) {
  test.describe(`axe, ${scheme} theme`, () => {
    test.beforeEach(async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme })
    })

    test('the empty screen has no serious or critical violation', async ({ page, api }) => {
      void api
      await page.goto('/')
      await expect(page.getByRole('heading', { name: 'No documents yet' })).toBeVisible()
      await expect(page.getByRole('heading', { name: 'Nothing to ask about yet' })).toBeVisible()
      expect(await seriousViolations(page)).toEqual([])
    })

    test('a loaded chat has no serious or critical violation', async ({ page, api }) => {
      api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [report] }]))
      api.on('POST', '/api/collections/default/chat', answer)
      await page.goto('/')
      await page.getByLabel('Ask a question').fill('How did revenue do?')
      await page.getByRole('button', { name: 'Ask' }).click()
      await expect(page.getByLabel('Answer details')).toBeVisible()
      expect(await seriousViolations(page)).toEqual([])
    })

    test('the open viewer has no serious or critical violation', async ({ page, api }) => {
      api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [report] }]))
      api.on('POST', '/api/collections/default/chat', answer)
      api.on('GET', '/api/documents/d1/pdf', pdf())
      await page.goto('/')
      await askAndOpen(page)
      expect(await seriousViolations(page)).toEqual([])
    })

    test('every control shows a focus ring when the keyboard reaches it', async ({
      page,
      api,
    }) => {
      api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [report] }]))
      api.on('POST', '/api/collections/default/chat', answer)
      await page.goto('/')
      await page.getByLabel('Ask a question').fill('How did revenue do?')
      await page.getByRole('button', { name: 'Ask' }).click()
      await expect(page.getByLabel('Answer details')).toBeVisible()
      await page.getByLabel('Ask a question').fill('Next question')
      await page.locator('body').click({ position: { x: 1, y: 1 } })

      // Tab through the whole page once, until focus comes back round to a control.
      const seen: string[] = []
      for (let i = 0; i < 40; i++) {
        await page.keyboard.press('Tab')
        const focused = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null
          if (!el || el === document.body) return null
          if (el.dataset.tabbed) return 'again'
          el.dataset.tabbed = 'yes'
          const style = getComputedStyle(el)
          return {
            name: el.outerHTML.slice(0, 80),
            outline: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2,
          }
        })
        if (focused === 'again') break
        if (!focused) continue
        seen.push(focused.name)
        expect(focused.outline, `focus ring on ${focused.name}`).toBe(true)
      }
      expect(seen.length).toBeGreaterThanOrEqual(8)
    })
  })
}

test('with reduced motion asked for, nothing animates', async ({ page, api }) => {
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  api.on('GET', '/api/collections', async (route) => {
    await gate
    await json([])(route)
  })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/')
  const spinner = page.locator('.spinner').first()
  await expect(spinner).toBeVisible()

  const moving = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('*')].filter((el) => {
      const s = getComputedStyle(el)
      const long = (d: string) => d.split(',').some((x) => parseFloat(x) > 0.01)
      return (s.animationName !== 'none' && long(s.animationDuration)) || long(s.transitionDuration)
    }).length,
  )
  expect(moving).toBe(0)
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior)).toBe(
    'auto',
  )

  // Without the preference the spinner does turn.
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await expect(spinner).toHaveCSS('animation-name', 'spin')
  release()
})

/** Press Tab (Shift+Tab when `back`) until `target` has focus. */
async function tabTo(page: Page, target: Locator, back = false) {
  for (let i = 0; i < 40; i++) {
    if (await target.evaluate((el) => el === document.activeElement)) return
    await page.keyboard.press(back ? 'Shift+Tab' : 'Tab')
  }
  throw new Error(`Tab never reached ${target}`)
}

test('keyboard only: upload, ask, open a citation and close it', async ({ page, api }) => {
  let documents: (typeof report)[] = []
  api.on('GET', '/api/collections', (route) => json([{ id: 1, name: 'default', documents }])(route))
  api.on('POST', '/api/collections/default/documents', async (route) => {
    documents = [report]
    await route.fulfill({ status: 201, json: { id: 'd1' } })
  })
  api.on('POST', '/api/collections/default/chat', answer)
  api.on('GET', '/api/documents/d1/pdf', pdf())
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'No documents yet' })).toBeVisible()

  // The skip link is first, and jumps to the chat.
  await page.keyboard.press('Tab')
  const skip = page.getByRole('link', { name: 'Skip to chat' })
  await expect(skip).toBeFocused()
  await expect(skip).toBeInViewport()

  // Upload: the file input opens its chooser from the keyboard, and the chosen file uploads.
  const file = page.getByLabel('PDF', { exact: true })
  await tabTo(page, file)
  const chooser = page.waitForEvent('filechooser')
  await page.keyboard.press('Space')
  await (await chooser).setFiles(SAMPLE_PDF_PATH)
  await expect(page.getByText('Ready: 2 pages')).toBeVisible()

  // Ask: Shift+Enter is a new line, Enter asks.
  const question = page.getByLabel('Ask a question')
  await tabTo(page, question)
  await page.keyboard.type('How did revenue')
  await page.keyboard.press('Shift+Enter')
  await page.keyboard.type('do?')
  await expect(question).toHaveValue('How did revenue\ndo?')
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('Answer details')).toBeVisible()
  await expect(question).toHaveValue('')

  // Open the citation.
  const chip = page.getByRole('button', { name: 'report.pdf, p. 2' })
  await tabTo(page, chip, true)
  await page.keyboard.press('Enter')
  const viewer = page.getByRole('dialog', { name: 'report.pdf, page 2' })
  await expect(viewer.getByText('Page 2 of 2')).toBeVisible()
  await expect(viewer).toBeFocused()

  // Focus stays in the viewer, both ways round.
  for (const key of ['Tab', 'Tab', 'Tab', 'Shift+Tab', 'Shift+Tab', 'Shift+Tab']) {
    await page.keyboard.press(key)
    expect(await viewer.evaluate((el) => el.contains(document.activeElement))).toBe(true)
  }

  // Escape closes it and puts focus back on the chip.
  await page.keyboard.press('Escape')
  await expect(viewer).toHaveCount(0)
  await expect(chip).toBeFocused()
})
