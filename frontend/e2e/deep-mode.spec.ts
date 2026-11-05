import type { APIRequestContext, Page } from '@playwright/test'
import { expect, json, test, type ApiMock } from './fixtures'

const collections = [{ id: 1, name: 'default', documents: [] }]

const lease = { id: 'd1', filename: 'lease.pdf' }
const addendum = { id: 'd2', filename: 'addendum.pdf' }

const step = (question: string, ...pages: [typeof lease, number][]) => ({
  type: 'step',
  question,
  pages: pages.map(([document, page]) => ({ document, page })),
})

const claim = (text: string, document: typeof lease, page: number) => ({
  type: 'claim',
  text,
  document,
  page,
  chunk_id: `${document.id}-${page}`,
  quote: 'a quote long enough',
})

const done = (cost: number | null) => ({
  type: 'done',
  mode: 'deep',
  model: 'reasoning-model',
  cost,
})

const NOTE =
  'Deep mode uses the reasoning model: it plans sub-questions and searches for each, so it is slower and can cost more.'

// Script a deep answer on the fake backend, whose stream is sent one batch at a
// time, and let the page's chat request through to it.
async function scripted(page: Page, api: ApiMock, request: APIRequestContext, batches: object[][]) {
  const question = `Compare the documents ${test.info().testId}`
  await request.put(`/api/_fake/chat/${encodeURIComponent(question)}`, { data: { batches } })
  const sent: unknown[] = []
  api.on('GET', '/api/collections', json(collections))
  api.on('POST', '/api/collections/default/chat', (route) => {
    sent.push(route.request().postDataJSON())
    return route.continue()
  })
  await page.goto('/')
  const next = async () =>
    expect((await request.post(`/api/_fake/chat/${encodeURIComponent(question)}/next`)).ok()).toBe(
      true,
    )
  return { question, sent, next }
}

test('deep mode shows its note before the run, the steps as they arrive, and the cost after done', async ({
  page,
  api,
  request,
}) => {
  const { question, sent, next } = await scripted(page, api, request, [
    [step('How long is the lease?', [lease, 3], [lease, 4])],
    [step('How long is the addendum?', [addendum, 1])],
    [
      claim('The lease runs two years.', lease, 3),
      claim('The addendum adds one year.', addendum, 1),
      done(0.0042),
    ],
  ])

  const deep = page.getByRole('switch', { name: 'Deep mode' })
  await expect(deep).not.toBeChecked()
  await expect(page.getByText(NOTE)).toHaveCount(0)
  await deep.check()
  // The note is there before anything is asked.
  await expect(page.getByText(NOTE)).toBeVisible()

  await page.getByLabel('Ask a question').fill(question)
  await page.getByRole('button', { name: 'Ask' }).click()

  const answer = page.getByRole('article', { name: 'Answer' })
  const steps = answer.getByRole('list', { name: 'Steps' }).getByRole('listitem')
  await expect(steps).toHaveText(['How long is the lease? lease.pdf, p. 3; lease.pdf, p. 4'])
  await expect(answer).toHaveAttribute('aria-busy', 'true')
  await expect(answer.getByLabel('Answer details')).toHaveCount(0)
  expect(sent).toEqual([{ question, mode: 'deep' }])

  await next()
  await expect(steps).toHaveText([
    'How long is the lease? lease.pdf, p. 3; lease.pdf, p. 4',
    'How long is the addendum? addendum.pdf, p. 1',
  ])
  await expect(answer.getByLabel('Answer details')).toHaveCount(0)

  await next()
  await expect(answer).toHaveAttribute('aria-busy', 'false')
  await expect(answer.getByRole('list', { name: 'Claims' }).getByRole('listitem')).toHaveText([
    'The lease runs two years. lease.pdf, p. 3',
    'The addendum adds one year. addendum.pdf, p. 1',
  ])
  // The steps stay, in the order they came.
  await expect(steps).toHaveCount(2)
  await expect(answer.getByLabel('Answer details')).toHaveText(
    'deep · reasoning-model · $0.0042',
  )
  await expect(answer.getByRole('alert')).toHaveCount(0)
})

test('a deep run with a null cost shows cost unknown, never 0', async ({ page, api, request }) => {
  const { question, next } = await scripted(page, api, request, [
    [step('Which document is newer?', [lease, 1], [addendum, 1])],
    [claim('The addendum is newer.', addendum, 1), done(null)],
  ])
  await page.getByRole('switch', { name: 'Deep mode' }).check()
  await page.getByLabel('Ask a question').fill(question)
  await page.getByRole('button', { name: 'Ask' }).click()

  const answer = page.getByRole('article', { name: 'Answer' })
  await expect(answer.getByRole('list', { name: 'Steps' }).getByRole('listitem')).toHaveText([
    'Which document is newer? lease.pdf, p. 1; addendum.pdf, p. 1',
  ])
  await next()
  const meta = answer.getByLabel('Answer details')
  await expect(meta).toHaveText('deep · reasoning-model · cost unknown')
  await expect(meta).not.toContainText('0')
})

test('a deep run that fails after its steps is shown as an error, with no cost', async ({
  page,
  api,
  request,
}) => {
  const { question, next } = await scripted(page, api, request, [
    [step('How long is the lease?', [lease, 3])],
    [{ type: 'error', message: 'the deep run went over its token budget' }],
  ])
  await page.getByRole('switch', { name: 'Deep mode' }).check()
  await page.getByLabel('Ask a question').fill(question)
  await page.getByRole('button', { name: 'Ask' }).click()

  const answer = page.getByRole('article', { name: 'Answer' })
  await expect(answer.getByRole('list', { name: 'Steps' }).getByRole('listitem')).toHaveCount(1)
  await next()
  await expect(answer.getByRole('alert')).toHaveText(
    'The answer failed: the deep run went over its token budget',
  )
  await expect(answer).toHaveAttribute('aria-busy', 'false')
  await expect(answer.getByRole('list', { name: 'Claims' })).toHaveCount(0)
  await expect(answer.getByLabel('Answer details')).toHaveCount(0)
})

test('a step with no pages says nothing was found, and markup in it is shown as text', async ({
  page,
  api,
  request,
}) => {
  const xss = '<img src=x onerror="window.__xss=1">'
  const { question, next } = await scripted(page, api, request, [
    [
      step(`What about <b>parking</b>? ${xss}`),
      step('And the deposit?', [{ id: 'd3', filename: `${xss}.pdf` }, 2]),
    ],
    [{ type: 'refusal', passages: [] }, done(null)],
  ])
  await page.getByRole('switch', { name: 'Deep mode' }).check()
  await page.getByLabel('Ask a question').fill(question)
  await page.getByRole('button', { name: 'Ask' }).click()

  const answer = page.getByRole('article', { name: 'Answer' })
  await expect(answer.getByRole('list', { name: 'Steps' }).getByRole('listitem')).toHaveText([
    `What about <b>parking</b>? ${xss} no pages found`,
    `And the deposit? ${xss}.pdf, p. 2`,
  ])
  await next()
  await expect(answer).toContainText('The documents do not answer this.')
  await expect(answer.locator('b, img')).toHaveCount(0)
  expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
})

test('a fast answer after a deep one asks in fast mode again', async ({ page, api }) => {
  const sent: unknown[] = []
  api.on('GET', '/api/collections', json(collections))
  api.on('POST', '/api/collections/default/chat', (route) => {
    sent.push(route.request().postDataJSON())
    return route.fulfill({
      headers: { 'Content-Type': 'application/x-ndjson' },
      body: `${JSON.stringify({ type: 'refusal', passages: [] })}\n${JSON.stringify(done(null))}\n`,
    })
  })
  await page.goto('/')
  const deep = page.getByRole('switch', { name: 'Deep mode' })

  await deep.check()
  await page.getByLabel('Ask a question').fill('one')
  await page.getByRole('button', { name: 'Ask' }).click()
  await expect(page.getByRole('article', { name: 'Answer' })).toHaveAttribute('aria-busy', 'false')
  await deep.uncheck()
  await expect(page.getByText(NOTE)).toHaveCount(0)
  await page.getByLabel('Ask a question').fill('two')
  await page.getByRole('button', { name: 'Ask' }).click()

  await expect.poll(() => sent.length).toBe(2)
  expect(sent).toEqual([
    { question: 'one', mode: 'deep' },
    { question: 'two', mode: 'fast' },
  ])
})
