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

  // The one collection picker, in the documents panel, picks where the chat asks.
  await expect(page.getByRole('combobox')).toHaveCount(1)
  await page.getByLabel('Collection', { exact: true }).selectOption({ label: 'contracts' })
  await page.getByLabel('Ask a question').fill(question)
  await page.getByRole('button', { name: 'Ask' }).click()

  const answer = page.getByRole('article', { name: 'Answer' })
  const claims = answer.getByRole('list', { name: 'Claims' }).getByRole('listitem')
  await expect(claims).toHaveCount(1)
  await expect(claims.nth(0)).toContainText('The lease runs for two years.')
  // One document so far: a short chip, and the name once, in the lead line.
  await expect(claims.nth(0).getByRole('note', { name: 'Citation' })).toHaveText('p. 3')
  await expect(answer.locator('.answer-lead')).toHaveText('Found in lease.pdf, p. 3')
  // The comma sits flush against the file name, in the laid-out line and in the rendered text.
  const lead = answer.locator('.answer-lead')
  const gap = await lead.evaluate((el) => {
    const name = el.querySelector('.filename')!.getBoundingClientRect()
    const comma = el.lastElementChild!.firstChild!
    const range = document.createRange()
    range.setStart(comma, 0)
    range.setEnd(comma, 1)
    return range.getBoundingClientRect().left - name.right
  })
  expect(Math.abs(gap)).toBeLessThan(1)
  expect(await lead.evaluate((el) => (el as HTMLElement).innerText)).toBe('Found in lease.pdf, p. 3')
  await expect(answer).toHaveAttribute('aria-busy', 'true')
  expect(sent).toEqual({ question, mode: 'fast' })

  expect((await next()).ok()).toBe(true)
  await expect(claims).toHaveCount(2)
  await expect(claims.nth(1)).toContainText('It can be extended once.')
  // Two documents: numbered chips and numbered sources under the answer.
  await expect(claims.nth(0).getByRole('note', { name: 'Citation' })).toHaveText('1 · p. 3')
  await expect(claims.nth(1).getByRole('note', { name: 'Citation' })).toHaveText('2 · p. 1')
  await expect(claims.nth(1).getByRole('button', { name: 'addendum.pdf, p. 1' })).toBeVisible()
  await expect(answer.getByRole('list', { name: 'Sources' }).getByRole('listitem')).toHaveText([
    '1 lease.pdf',
    '2 addendum.pdf',
  ])
  await expect(answer.locator('.answer-lead')).toHaveText('Found in 2 documents, 2 points')
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
  await expect(answer.getByRole('listitem')).toHaveText(['Half an answer. p. 2'])
  await expect(answer).toContainText('Source: lease.pdf')
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
