import { test as base, expect, type Route } from '@playwright/test'
import { fileURLToPath } from 'node:url'

// The committed fixture PDF (two pages; text in fixtures/make-sample-pdf.mjs).
export const SAMPLE_PDF_PATH = fileURLToPath(
  new URL('./fixtures/sample.pdf', import.meta.url),
)

export type MockHandler = (route: Route) => Promise<void>

export const json =
  (body: unknown, status = 200): MockHandler =>
  (route) =>
    route.fulfill({ status, json: body })

export const pdf =
  (path = SAMPLE_PDF_PATH): MockHandler =>
  (route) =>
    route.fulfill({
      path,
      headers: {
        'Content-Type': 'application/pdf',
        'X-Content-Type-Options': 'nosniff',
      },
    })

export interface ApiMock {
  /** Answer `METHOD /api/...` (path without the query string) with `handler`. */
  on(method: string, path: string, handler: MockHandler): void
  /** Same-origin `/api` requests the page made, as `METHOD /api/...`. */
  readonly calls: string[]
}

// Every test gets every `/api/**` request from the page answered by a route
// fixture: `GET /api/health` and `GET /api/collections` (empty) by default, the rest by `api.on(...)`. A request
// with no mock gets a 501 and fails the test, so no test ever reaches a backend.
export const test = base.extend<{ api: ApiMock }>({
  api: [
    async ({ page, baseURL }, use) => {
      const handlers = new Map<string, MockHandler>([
        ['GET /api/health', json({ status: 'ok' })],
        ['GET /api/collections', json([])],
      ])
      const calls: string[] = []
      const unmocked: string[] = []

      await page.route(`${baseURL}/api/**`, async (route) => {
        const request = route.request()
        const key = `${request.method()} ${new URL(request.url()).pathname}`
        calls.push(key)
        const handler = handlers.get(key)
        if (handler) return handler(route)
        unmocked.push(key)
        return route.fulfill({ status: 501, json: { detail: `no mock for ${key}` } })
      })

      await use({
        on: (method, path, handler) => handlers.set(`${method} ${path}`, handler),
        calls,
      })

      expect(unmocked, 'API requests with no route fixture').toEqual([])
    },
    { auto: true },
  ],
})

export { expect }
