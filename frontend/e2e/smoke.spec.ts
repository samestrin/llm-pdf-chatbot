import { expect, json, pdf, test } from './fixtures'

test('loads the app and checks the backend health at same-origin /api', async ({
  page,
  api,
  baseURL,
}) => {
  const health = page.waitForRequest(`${baseURL}/api/health`)
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'LLM PDF Chatbot' })).toBeVisible()
  expect((await health).url()).toBe(`${baseURL}/api/health`)
  // A healthy backend is not mentioned.
  await expect(page.getByRole('heading', { name: 'No documents yet' })).toBeVisible()
  await expect(page.getByRole('banner')).not.toContainText('Backend')
  // React StrictMode runs the effect twice in dev, so the call can repeat.
  expect(new Set(api.calls)).toEqual(new Set(['GET /api/health', 'GET /api/collections']))
})

test('shows the backend as down when /api/health fails, with a Retry', async ({ page, api }) => {
  api.on('GET', '/api/health', json({ detail: 'database unavailable' }, 503))
  await page.goto('/')

  const banner = page.getByRole('banner')
  await expect(banner.getByRole('status')).toHaveText('Backend is down')
  api.on('GET', '/api/health', json({ status: 'ok' }))
  await banner.getByRole('button', { name: 'Retry' }).click()
  await expect(banner).not.toContainText('Backend')
})

test('serves the committed fixture PDF through a route fixture', async ({ page, api }) => {
  api.on('GET', '/api/fixture.pdf', pdf())
  await page.goto('/')

  const got = await page.evaluate(async () => {
    const res = await fetch('/api/fixture.pdf')
    const bytes = new Uint8Array(await res.arrayBuffer())
    return {
      type: res.headers.get('content-type'),
      head: String.fromCharCode(...bytes.slice(0, 5)),
    }
  })
  expect(got).toEqual({ type: 'application/pdf', head: '%PDF-' })
})

test('the dev server proxies /api to the backend with the prefix stripped', async ({
  request,
}) => {
  // `request` does not go through the page's route fixtures: this reaches the
  // Vite proxy, which must forward `/api/health` to the backend's `/health`.
  const res = await request.get('/api/health')

  expect(res.status()).toBe(200)
  expect(await res.json()).toEqual({ status: 'ok', server: 'fake-backend' })
})
