import { expect, test } from './fixtures'

const backgroundOf = (page: import('@playwright/test').Page) =>
  page.evaluate(() => getComputedStyle(document.body).backgroundColor)

for (const scheme of ['light', 'dark'] as const) {
  test(`at 375 px the ${scheme} scheme has no horizontal scroll`, async ({ page, api }) => {
    void api
    await page.setViewportSize({ width: 375, height: 800 })
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('/')
    await expect(page.getByRole('status')).toHaveText('Backend: ok')

    const widths = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    expect(widths.scroll).toBeLessThanOrEqual(widths.client)
    expect(await page.getByRole('button', { name: 'Dark theme' }).getAttribute('aria-pressed')).toBe(
      String(scheme === 'dark'),
    )
  })
}

test('the colours follow the system scheme', async ({ page, api }) => {
  void api
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/')
  const light = await backgroundOf(page)
  await page.emulateMedia({ colorScheme: 'dark' })
  expect(await backgroundOf(page)).not.toBe(light)
})

test('the toggle overrides the system scheme and survives a reload', async ({ page, api }) => {
  void api
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/')
  const light = await backgroundOf(page)

  await page.getByRole('button', { name: 'Dark theme' }).click()
  const dark = await backgroundOf(page)
  expect(dark).not.toBe(light)

  await page.reload()
  expect(await backgroundOf(page)).toBe(dark)
  await expect(page.getByRole('button', { name: 'Dark theme' })).toHaveAttribute('aria-pressed', 'true')

  // The saved choice still wins when the system scheme changes.
  await page.emulateMedia({ colorScheme: 'light' })
  expect(await backgroundOf(page)).toBe(dark)
})

test('clicking the toggle again returns to light and that survives a reload', async ({
  page,
  api,
}) => {
  void api
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/')
  const light = await backgroundOf(page)
  const toggle = page.getByRole('button', { name: 'Dark theme' })

  await toggle.click()
  expect(await backgroundOf(page)).not.toBe(light)
  await toggle.click()

  expect(await backgroundOf(page)).toBe(light)
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
  expect(await page.evaluate(() => localStorage.getItem('theme'))).toBe('light')
  await page.reload()
  expect(await backgroundOf(page)).toBe(light)
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
})

test('a saved light choice beats a dark system scheme after a reload', async ({ page, api }) => {
  void api
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/')
  const light = await backgroundOf(page)

  await page.emulateMedia({ colorScheme: 'dark' })
  const systemDark = await backgroundOf(page)
  expect(systemDark).not.toBe(light)
  // System is dark, so the button reads pressed; one click saves light.
  await page.reload()
  const toggle = page.getByRole('button', { name: 'Dark theme' })
  await expect(toggle).toHaveAttribute('aria-pressed', 'true')
  await toggle.click()
  expect(await backgroundOf(page)).toBe(light)

  await page.reload()
  expect(await backgroundOf(page)).toBe(light)
  await expect(toggle).toHaveAttribute('aria-pressed', 'false')
})
