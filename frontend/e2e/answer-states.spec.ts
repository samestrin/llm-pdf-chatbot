import type { Page } from '@playwright/test'
import { expect, json, pdf, test, type ApiMock, type MockHandler } from './fixtures'

const collections = [{ id: 1, name: 'default', documents: [] }]

const doc = { id: 'd1', filename: 'lease.pdf' }

const claim = (text: string, quote = 'a quote', document = doc) => ({
  type: 'claim',
  text,
  document,
  page: 2,
  chunk_id: 'd1-2',
  quote,
})

const done = (cost: number | null, model = 'fake-model') => ({
  type: 'done',
  mode: 'fast',
  model,
  cost,
})

const ndjson =
  (...events: object[]): MockHandler =>
  (route) =>
    route.fulfill({
      headers: { 'Content-Type': 'application/x-ndjson' },
      body: events.map((e) => `${JSON.stringify(e)}\n`).join(''),
    })

async function ask(
  page: Page,
  api: ApiMock,
  answer: MockHandler,
  question = 'What is the rent?',
) {
  api.on('GET', '/api/collections', json(collections))
  api.on('POST', '/api/collections/default/chat', answer)
  await page.goto('/')
  await page.getByLabel('Ask a question').fill(question)
  await page.getByRole('button', { name: 'Ask' }).click()
  return page.getByRole('article', { name: 'Answer' })
}

test('a refusal says so plainly and lists the closest passages', async ({ page, api }) => {
  const answer = await ask(
    page,
    api,
    ndjson(
      {
        type: 'refusal',
        passages: [
          { document: doc, page: 4, chunk_id: 'd1-4', text: 'The rent is reviewed each year.' },
          {
            document: { id: 'd2', filename: 'addendum.pdf' },
            page: 1,
            chunk_id: 'd2-1',
            text: 'Parking is extra.',
          },
        ],
      },
      done(0.0021),
    ),
  )

  await expect(answer.getByRole('heading', { name: 'Not in your documents' })).toBeVisible()
  await expect(answer).toContainText('The documents do not answer this.')
  const passages = answer.getByRole('list', { name: 'Closest passages' }).getByRole('listitem')
  await expect(passages).toHaveCount(2)
  await expect(passages.nth(0)).toContainText('lease.pdf, p. 4')
  await expect(passages.nth(0).getByRole('blockquote')).toHaveText(
    'The rent is reviewed each year.',
  )
  await expect(passages.nth(1)).toContainText('addendum.pdf, p. 1')
  await expect(passages.nth(1).getByRole('blockquote')).toHaveText('Parking is extra.')
  await expect(answer.getByRole('alert')).toHaveCount(0)
  await expect(answer.getByLabel('Answer details')).toHaveText('fast · fake-model · $0.0021')
})

test('a refusal with no close passages says that too', async ({ page, api }) => {
  const answer = await ask(page, api, ndjson({ type: 'refusal', passages: [] }, done(null)))

  await expect(answer).toContainText('The documents do not answer this.')
  await expect(answer).toContainText('No passage came close.')
  await expect(answer.getByRole('alert')).toHaveCount(0)
})

test('an error event is shown as an error, after whatever came before it', async ({
  page,
  api,
}) => {
  const answer = await ask(
    page,
    api,
    ndjson(claim('The lease runs two years.'), { type: 'error', message: 'model timed out' }),
  )

  await expect(answer.getByRole('listitem')).toHaveText([
    'The lease runs two years. lease.pdf, p. 2',
  ])
  await expect(answer.getByRole('alert')).toHaveText('The answer failed: model timed out')
  await expect(answer).toHaveAttribute('aria-busy', 'false')
  await expect(answer.getByLabel('Answer details')).toHaveCount(0)
})

test('an error event with no message is still shown, never an empty answer', async ({
  page,
  api,
}) => {
  const answer = await ask(page, api, ndjson({ type: 'error', message: '' }))
  await expect(answer.getByRole('alert')).toHaveText('The answer failed.')
})

test('an answer that finishes with nothing in it is shown as an error', async ({
  page,
  api,
}) => {
  const answer = await ask(page, api, ndjson(done(null)))
  await expect(answer.getByRole('alert')).toHaveText('The answer came back empty.')
  await expect(answer.getByLabel('Answer details')).toHaveText('fast · fake-model · cost unknown')
})

test('a stream cut off halfway keeps what arrived and says it stopped', async ({
  page,
  api,
}) => {
  const answer = await ask(page, api, ndjson(claim('Half an answer.')))

  await expect(answer.getByRole('listitem')).toHaveText(['Half an answer. lease.pdf, p. 2'])
  await expect(answer.getByRole('alert')).toHaveText('The answer stopped before it finished.')
  await expect(answer).toHaveAttribute('aria-busy', 'false')
  await expect(answer.getByLabel('Answer details')).toHaveCount(0)
  // The next question can be asked.
  await page.getByLabel('Ask a question').fill('Again?')
  await expect(page.getByRole('button', { name: 'Ask' })).toBeEnabled()
})

test('stopping an answer midway shows it as stopped, and the next one can be asked', async ({
  page,
  api,
  request,
}) => {
  const question = `Stop me ${test.info().testId}`
  // The fake backend holds the stream open after the first batch.
  await request.put(`/api/_fake/chat/${encodeURIComponent(question)}`, {
    data: { batches: [[claim('The first part.')], [done(null)]] },
  })
  const answer = await ask(page, api, (route) => route.continue(), question)

  await expect(answer.getByRole('listitem')).toHaveCount(1)
  await expect(answer).toHaveAttribute('aria-busy', 'true')
  await expect(page.getByRole('button', { name: 'Ask' })).toBeDisabled()
  await page.getByRole('button', { name: 'Stop' }).click()

  await expect(answer).toHaveAttribute('aria-busy', 'false')
  await expect(answer.getByRole('status')).toHaveText(
    'You stopped this answer before it finished.',
  )
  await expect(answer.getByRole('listitem')).toHaveText(['The first part. lease.pdf, p. 2'])
  await expect(answer.getByLabel('Answer details')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Stop' })).toHaveCount(0)
  await page.getByLabel('Ask a question').fill('Next?')
  await expect(page.getByRole('button', { name: 'Ask' })).toBeEnabled()
})

test('a null cost is shown as cost unknown, never as 0', async ({ page, api }) => {
  const answer = await ask(page, api, ndjson(claim('Two years.'), done(null)))

  const meta = answer.getByLabel('Answer details')
  await expect(meta).toHaveText('fast · fake-model · cost unknown')
  await expect(meta).not.toContainText('0')
})

test('a known cost is shown, including a real zero', async ({ page, api }) => {
  const answer = await ask(page, api, ndjson(claim('Two years.'), done(0, 'local-model')))
  await expect(answer.getByLabel('Answer details')).toHaveText('fast · local-model · $0')
})

test('markup in model, quote, file-name and passage text is shown as text', async ({
  page,
  api,
}) => {
  const xss = '<img src=x onerror="window.__xss=1">'
  const answer = await ask(
    page,
    api,
    ndjson(
      claim(`Rent is <b>high</b> ${xss}`, `the <i>quote</i> ${xss}`, {
        id: 'd1',
        filename: `<script>window.__xss=1</script>.pdf`,
      }),
      done(null, `<u>model</u>${xss}`),
    ),
  )

  await expect(answer.getByRole('listitem')).toContainText(`Rent is <b>high</b> ${xss}`)
  const chip = answer.getByRole('note', { name: 'Citation' })
  await expect(chip).toHaveText('<script>window.__xss=1</script>.pdf, p. 2')
  await expect(chip).toHaveAttribute('title', `the <i>quote</i> ${xss}`)
  await expect(answer.getByLabel('Answer details')).toHaveText(
    `fast · <u>model</u>${xss} · cost unknown`,
  )
  await expect(answer.locator('b, i, u, img, script')).toHaveCount(0)

  // The viewer shows the quote, and the file name in its title, as text too.
  api.on('GET', '/api/documents/d1/pdf', pdf())
  await chip.getByRole('button').click()
  const viewer = page.getByRole('dialog', {
    name: '<script>window.__xss=1</script>.pdf, page 2',
  })
  await expect(viewer.getByRole('blockquote')).toHaveText(`the <i>quote</i> ${xss}`)
  await expect(viewer.locator('img, script, i')).toHaveCount(0)
  expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
})

test('markup in a refusal passage and an error message is shown as text', async ({
  page,
  api,
}) => {
  const xss = '<img src=x onerror="window.__xss=1">'
  const answer = await ask(
    page,
    api,
    ndjson(
      {
        type: 'refusal',
        passages: [
          {
            document: { id: 'd1', filename: `${xss}.pdf` },
            page: 1,
            chunk_id: 'd1-1',
            text: `<b>bold</b>${xss}`,
          },
        ],
      },
      { type: 'error', message: `<em>boom</em>${xss}` },
    ),
  )

  await expect(answer.getByRole('blockquote')).toHaveText(`<b>bold</b>${xss}`)
  await expect(answer.getByRole('listitem')).toContainText(`${xss}.pdf, p. 1`)
  await expect(answer.getByRole('alert')).toHaveText(`The answer failed: <em>boom</em>${xss}`)
  await expect(answer.locator('b, em, img')).toHaveCount(0)
  expect(await page.evaluate(() => (window as { __xss?: unknown }).__xss)).toBeUndefined()
})
