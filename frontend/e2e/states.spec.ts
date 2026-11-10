import type { Page } from '@playwright/test'
import { expect, json, pdf, SAMPLE_PDF_PATH, test, type ApiMock, type MockHandler } from './fixtures'

// Empty, loading, error-with-retry and still-reading states of the documents
// panel, the chat and the PDF viewer.

const doc = (id: string, filename: string, state = 'ready') => ({
  id,
  filename,
  pages: 2,
  size_bytes: 921,
  status: {
    state,
    error: null,
    pages_total: 2,
    pages_read: state === 'ready' ? 2 : 1,
    pages_failed: 0,
    failures: [],
  },
})

/** A handler that waits for `release()` before answering with `then`. */
function gated(then: MockHandler) {
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  const handler: MockHandler = async (route) => {
    await gate
    await then(route)
  }
  return { handler, release }
}

/** Answer the first call with `first` and every later one with `rest`. */
function firstThen(first: MockHandler, rest: MockHandler): MockHandler {
  let calls = 0
  return (route) => (calls++ === 0 ? first(route) : rest(route))
}

const documentsPanel = (page: Page) => page.getByRole('complementary', { name: 'Documents' })
const chat = (page: Page) => page.getByRole('region', { name: 'Chat' })

test.describe('documents panel', () => {
  test('first run says what to do next', async ({ page, api }) => {
    api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [] }]))
    await page.goto('/')

    const panel = documentsPanel(page)
    await expect(panel.getByRole('heading', { name: 'No documents yet' })).toBeVisible()
    await expect(panel).toContainText(
      'Drop a PDF on this panel or choose one above. Once it is read, ask about it in the chat.',
    )
    await expect(panel.getByText('No documents.')).toHaveCount(0)
  })

  test('shows a spinner while the documents load', async ({ page, api }) => {
    const load = gated(json([{ id: 1, name: 'default', documents: [doc('d1', 'report.pdf')] }]))
    api.on('GET', '/api/collections', load.handler)
    await page.goto('/')

    const panel = documentsPanel(page)
    await expect(panel.getByText('Loading documents…')).toBeVisible()
    await expect(panel).toHaveAttribute('aria-busy', 'true')
    load.release()
    await expect(panel.getByText('report.pdf', { exact: true })).toBeVisible()
    await expect(panel.getByText('Loading documents…')).toHaveCount(0)
    await expect(panel).toHaveAttribute('aria-busy', 'false')
  })

  test('retries a failed load', async ({ page, api }) => {
    api.on(
      'GET',
      '/api/collections',
      firstThen(
        json({ detail: 'database is down' }, 503),
        json([{ id: 1, name: 'default', documents: [doc('d1', 'report.pdf')] }]),
      ),
    )
    await page.goto('/')

    const panel = documentsPanel(page)
    await expect(panel.getByRole('alert')).toHaveText('Could not load documents: database is down')
    // Whether there are documents is not known, so neither panel says there are none.
    await expect(panel.getByRole('heading', { name: 'No documents yet' })).toHaveCount(0)
    await expect(chat(page).getByRole('heading', { name: 'Nothing to ask about yet' })).toHaveCount(0)
    await panel.getByRole('button', { name: 'Retry loading documents' }).click()
    await expect(panel.getByText('report.pdf', { exact: true })).toBeVisible()
    await expect(panel.getByRole('alert')).toHaveCount(0)
  })

  test('retries a failed upload', async ({ page, api }) => {
    let documents: ReturnType<typeof doc>[] = []
    api.on('GET', '/api/collections', (route) =>
      json([{ id: 1, name: 'default', documents }])(route),
    )
    api.on(
      'POST',
      '/api/collections/default/documents',
      firstThen(json({ detail: 'storage is full' }, 507), async (route) => {
        documents = [doc('n', 'sample.pdf')]
        await route.fulfill({ status: 201, json: { id: 'n' } })
      }),
    )
    await page.goto('/')

    const panel = documentsPanel(page)
    await panel.locator('input[type=file]').setInputFiles(SAMPLE_PDF_PATH)
    await expect(panel.getByRole('alert')).toHaveText('storage is full')
    await panel.getByRole('button', { name: 'Retry upload' }).click()

    await expect(panel.getByText('sample.pdf', { exact: true })).toBeVisible()
    await expect(panel.getByRole('alert')).toHaveCount(0)
    await expect(panel.getByRole('button', { name: 'Retry upload' })).toHaveCount(0)
  })

  test('says a document still being read cannot be asked about yet', async ({ page, api }) => {
    let polls = 0
    api.on('GET', '/api/collections', (route) => {
      polls += 1
      const state = polls < 3 ? 'processing' : 'ready'
      return json([{ id: 1, name: 'default', documents: [doc('d1', 'report.pdf', state)] }])(route)
    })
    await page.goto('/')

    const item = documentsPanel(page).getByRole('listitem', { name: 'report.pdf' })
    await expect(item).toContainText('Reading: 1 of 2 pages')
    await expect(item).toContainText('Still being read. You can ask about it once it is ready.')
    await expect(item).toContainText('Ready: 2 pages', { timeout: 10_000 })
    await expect(item).not.toContainText('Still being read')
  })
})

test.describe('chat', () => {
  test('first run says what to do next', async ({ page }) => {
    // The fixture's default `GET /api/collections` answers with no collection.
    await page.goto('/')

    const panel = chat(page)
    await expect(panel.getByRole('heading', { name: 'Nothing to ask about yet' })).toBeVisible()
    await expect(panel).toContainText(
      'Upload a PDF in the Documents panel, then ask a question about it here.',
    )
    await expect(panel.getByRole('button', { name: 'Ask' })).toBeDisabled()
  })

  test('shows a spinner until the documents have loaded', async ({ page, api }) => {
    const load = gated(json([{ id: 1, name: 'default', documents: [] }]))
    api.on('GET', '/api/collections', load.handler)
    await page.goto('/')

    const panel = chat(page)
    await expect(panel.getByText('Loading your documents…')).toBeVisible()
    await expect(panel.getByRole('heading', { name: 'Nothing to ask about yet' })).toHaveCount(0)
    load.release()
    await expect(panel.getByRole('heading', { name: 'Nothing to ask about yet' })).toBeVisible()
    await expect(panel.getByText('Loading your documents…')).toHaveCount(0)
  })

  test('retries an answer whose stream failed', async ({ page, api, request }) => {
    const question = `What is the rent? ${test.info().testId}`
    api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [doc('d1', 'lease.pdf')] }]))
    // Let the chat request through to the fake backend. It has no scripted
    // answer for the question yet, so the first ask fails with its 404.
    api.on('POST', '/api/collections/default/chat', (route) => route.continue())
    await page.goto('/')
    await page.getByLabel('Ask a question').fill(question)
    await page.getByRole('button', { name: 'Ask' }).click()

    const answer = page.getByRole('article', { name: 'Answer' })
    await expect(answer.getByRole('alert')).toHaveText(
      `The answer failed: no scripted answer for ${question}`,
    )
    await request.put(`/api/_fake/chat/${encodeURIComponent(question)}`, {
      data: {
        batches: [
          [
            {
              type: 'claim',
              text: 'The rent is 900 a month.',
              document: { id: 'd1', filename: 'lease.pdf' },
              page: 2,
              chunk_id: 'd1-2',
              quote: '900',
            },
            { type: 'done', mode: 'fast', model: 'fake-model', cost: null },
          ],
        ],
      },
    })
    await answer.getByRole('button', { name: 'Retry' }).click()

    await expect(answer.getByRole('list', { name: 'Claims' })).toContainText(
      'The rent is 900 a month.',
    )
    await expect(answer.getByRole('alert')).toHaveCount(0)
    await expect(answer.getByRole('button', { name: 'Retry' })).toHaveCount(0)
    await expect(page.getByRole('article', { name: 'Answer' })).toHaveCount(1)
  })

  test('says which documents are still being read', async ({ page, api }) => {
    let polls = 0
    api.on('GET', '/api/collections', (route) => {
      polls += 1
      const state = polls < 3 ? 'queued' : 'ready'
      return json([
        {
          id: 1,
          name: 'default',
          documents: [doc('d1', 'lease.pdf'), doc('d2', 'addendum.pdf', state)],
        },
      ])(route)
    })
    await page.goto('/')

    const note = chat(page).getByText(
      'Still reading addendum.pdf. Answers leave it out until it is ready.',
    )
    await expect(note).toBeVisible()
    await expect(note).toHaveCount(0, { timeout: 10_000 })
  })
})

test.describe('PDF viewer', () => {
  async function openCitation(page: Page, api: ApiMock) {
    api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [doc('d1', 'report.pdf')] }]))
    api.on('POST', '/api/collections/default/chat', (route) =>
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
      }),
    )
    await page.goto('/')
    await page.getByLabel('Ask a question').fill('How did revenue do?')
    await page.getByRole('button', { name: 'Ask' }).click()
    await page.getByRole('button', { name: 'report.pdf, p. 2' }).click()
    return page.getByRole('dialog', { name: 'report.pdf, page 2' })
  }

  test('shows a spinner while the page opens', async ({ page, api }) => {
    const file = gated(pdf())
    api.on('GET', '/api/documents/d1/pdf', file.handler)
    const viewer = await openCitation(page, api)

    await expect(viewer.getByText('Opening page 2…')).toBeVisible()
    await expect(viewer).toHaveAttribute('aria-busy', 'true')
    file.release()
    await expect(viewer.getByText('Page 2 of 2')).toBeVisible()
    await expect(viewer.getByText('Opening page 2…')).toHaveCount(0)
    await expect(viewer).toHaveAttribute('aria-busy', 'false')
  })

  test('retries a PDF that failed to open', async ({ page, api }) => {
    api.on('GET', '/api/documents/d1/pdf', firstThen(json({ detail: 'file not found' }, 404), pdf()))
    const viewer = await openCitation(page, api)

    await expect(viewer.getByRole('alert')).toContainText('Could not open report.pdf')
    await viewer.getByRole('button', { name: 'Retry' }).click()

    await expect(viewer.getByText('Page 2 of 2')).toBeVisible()
    await expect(viewer.locator('.textLayer')).toContainText('third quarter')
    await expect(viewer.getByRole('alert')).toHaveCount(0)
    await expect(viewer.getByRole('button', { name: 'Retry' })).toHaveCount(0)
  })
})
