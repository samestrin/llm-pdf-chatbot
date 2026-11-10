import { expect, test } from './fixtures'

const backgroundOf = (page: import('@playwright/test').Page) =>
  page.evaluate(() => getComputedStyle(document.body).backgroundColor)

for (const scheme of ['light', 'dark'] as const) {
  test(`at 375 px the ${scheme} scheme has no horizontal scroll`, async ({ page, api }) => {
    void api
    await page.setViewportSize({ width: 375, height: 800 })
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'No documents yet' })).toBeVisible()
    await expect(page.getByRole('banner')).not.toContainText('Backend')

    const widths = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    expect(widths.scroll).toBeLessThanOrEqual(widths.client)
    // The button names what it does next.
    await expect(
      page.getByRole('button', { name: scheme === 'dark' ? 'Light theme' : 'Dark theme' }),
    ).toBeVisible()
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
  await expect(page.getByRole('button', { name: 'Light theme' })).toBeVisible()

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
  const toggle = page.locator('.theme-toggle')

  await expect(toggle).toHaveText('Dark theme')
  await toggle.click()
  expect(await backgroundOf(page)).not.toBe(light)
  await expect(toggle).toHaveText('Light theme')
  await toggle.click()

  expect(await backgroundOf(page)).toBe(light)
  await expect(toggle).toHaveText('Dark theme')
  expect(await page.evaluate(() => localStorage.getItem('theme'))).toBe('light')
  await page.reload()
  expect(await backgroundOf(page)).toBe(light)
  await expect(toggle).toHaveText('Dark theme')
})

test('a saved light choice beats a dark system scheme after a reload', async ({ page, api }) => {
  void api
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/')
  const light = await backgroundOf(page)

  await page.emulateMedia({ colorScheme: 'dark' })
  const systemDark = await backgroundOf(page)
  expect(systemDark).not.toBe(light)
  // System is dark, so the button offers light; one click saves light.
  await page.reload()
  const toggle = page.locator('.theme-toggle')
  await expect(toggle).toHaveText('Light theme')
  await toggle.click()
  expect(await backgroundOf(page)).toBe(light)

  await page.reload()
  expect(await backgroundOf(page)).toBe(light)
  await expect(toggle).toHaveText('Dark theme')
})

for (const scheme of ['light', 'dark'] as const) {
  test(`in the ${scheme} scheme an enabled Ask looks enabled and a disabled one looks different`, async ({
    page,
    api,
  }) => {
    void api
    await page.emulateMedia({ colorScheme: scheme })
    await page.goto('/')
    const ask = page.getByRole('button', { name: 'Ask' })
    const look = () =>
      ask.evaluate((el) => {
        const s = getComputedStyle(el)
        return { background: s.backgroundColor, color: s.color, opacity: s.opacity, cursor: s.cursor }
      })

    await expect(ask).toBeDisabled()
    const disabled = await look()
    await page.getByLabel('Ask a question').fill('Anything?')
    await expect(ask).toBeEnabled()
    const enabled = await look()

    // Not just a lower opacity: a different fill and text colour, both fully opaque.
    expect(disabled.opacity).toBe('1')
    expect(enabled.opacity).toBe('1')
    expect(disabled.background).not.toBe(enabled.background)
    expect(disabled.color).not.toBe(enabled.color)
    expect(disabled.cursor).toBe('not-allowed')
    expect(enabled.cursor).toBe('pointer')
    // The enabled fill is the solid primary: white text on it.
    expect(enabled.color).toBe('rgb(255, 255, 255)')
  })
}
