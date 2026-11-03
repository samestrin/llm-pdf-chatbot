import { expect, json, test } from './fixtures'

const ready = (id: string, filename: string, pages: number) => ({
  id,
  filename,
  pages,
  size_bytes: 1000,
  status: {
    state: 'ready',
    error: null,
    pages_total: pages,
    pages_read: pages,
    pages_failed: 0,
    failures: [],
  },
})

const collections = [
  { id: 1, name: 'default', documents: [] },
  {
    id: 2,
    name: 'contracts',
    documents: [ready('d1', 'lease.pdf', 4), ready('d2', 'addendum.pdf', 2)],
  },
]

const claim = (text: string, id: string, filename: string, page: number, quote: string) => ({
  type: 'claim',
  text,
  document: { id, filename },
  page,
  chunk_id: `${id}-${page}`,
  quote,
})

test('streams an answer claim by claim, each with its citation chip', async ({
  page,
  api,
  request,
}) => {
  const question = `How long is the lease? ${test.info().testId}`
  // Scripted on the fake backend; the page's chat request is let through the
  // route fixture to the Vite proxy, so the body really streams.
  await request.put(`/api/_fake/chat/${encodeURIComponent(question)}`, {
    data: {
      batches: [
        [claim('The lease runs for two years.', 'd1', 'lease.pdf', 3, 'a term of two (2) years')],
        [claim('It can be extended once.', 'd2', 'addendum.pdf', 1, 'one renewal')],
        [{ type: 'done', mode: 'fast', model: 'fake-model', cost: null }],
      ],
    },
  })
  const next = () => request.post(`/api/_fake/chat/${encodeURIComponent(question)}/next`)
  api.on('GET', '/api/collections', json(collections))
  let sent: unknown
  api.on('POST', '/api/collections/contracts/chat', (route) => {
    sent = route.request().postDataJSON()
    return route.continue()
  })
  await page.goto('/')

  await page.getByLabel('Ask in collection').selectOption('contracts')
  await page.getByLabel('Ask a question').fill(question)
  await page.getByRole('button', { name: 'Ask' }).click()

  const answer = page.getByRole('article', { name: 'Answer' })
  const claims = answer.getByRole('listitem')
  await expect(claims).toHaveCount(1)
  await expect(claims.nth(0)).toContainText('The lease runs for two years.')
  await expect(claims.nth(0).getByRole('note', { name: 'Citation' })).toHaveText('lease.pdf, p. 3')
  await expect(answer).toHaveAttribute('aria-busy', 'true')
  expect(sent).toEqual({ question, mode: 'fast' })

  expect((await next()).ok()).toBe(true)
  await expect(claims).toHaveCount(2)
  await expect(claims.nth(1)).toContainText('It can be extended once.')
  await expect(claims.nth(1).getByRole('note', { name: 'Citation' })).toHaveText(
    'addendum.pdf, p. 1',
  )
  await expect(claims.nth(1).getByRole('note', { name: 'Citation' })).toHaveAttribute(
    'title',
    'one renewal',
  )
  await expect(answer).toHaveAttribute('aria-busy', 'true')

  expect((await next()).ok()).toBe(true)
  await expect(answer).toHaveAttribute('aria-busy', 'false')
  await expect(page.getByRole('article', { name: 'Question' })).toHaveText(question)
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('a stream that ends without done is shown as cut off, not complete', async ({
  page,
  api,
}) => {
  api.on('GET', '/api/collections', json(collections))
  api.on('POST', '/api/collections/default/chat', (route) =>
    route.fulfill({
      headers: { 'Content-Type': 'application/x-ndjson' },
      body: `${JSON.stringify(claim('Half an answer.', 'd1', 'lease.pdf', 2, 'half'))}\n`,
    }),
  )
  await page.goto('/')

  await page.getByLabel('Ask a question').fill('What is the rent?')
  await page.getByRole('button', { name: 'Ask' }).click()

  const answer = page.getByRole('article', { name: 'Answer' })
  await expect(answer.getByRole('listitem')).toHaveText(['Half an answer. lease.pdf, p. 2'])
  await expect(answer.getByRole('alert')).toHaveText(
    'The answer stopped before it finished.',
  )
})

test('shows the backend error when the chat request is rejected', async ({ page, api }) => {
  api.on('GET', '/api/collections', json(collections))
  api.on('POST', '/api/collections/default/chat', json({ detail: 'mode must be fast' }, 422))
  await page.goto('/')

  await page.getByLabel('Ask a question').fill('Anything?')
  await page.getByRole('button', { name: 'Ask' }).click()

  await expect(page.getByRole('article', { name: 'Answer' }).getByRole('alert')).toHaveText(
    'The answer failed: mode must be fast',
  )
})
