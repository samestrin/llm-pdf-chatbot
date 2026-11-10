import { expect, json, SAMPLE_PDF_PATH, test } from './fixtures'

const status = (over: object) => ({
  state: 'ready',
  error: null,
  pages_total: 3,
  pages_read: 3,
  pages_failed: 0,
  failures: [],
  ...over,
})

const doc = (id: string, filename: string, s: object) => ({
  id,
  filename,
  pages: 3,
  size_bytes: 1000,
  status: status(s),
})

test('shows ingestion progress, a partly failed document with its failed pages, and deletes', async ({
  page,
  api,
}) => {
  let documents = [
    doc('a', 'reading.pdf', { state: 'processing', pages_read: 1 }),
    doc('b', 'scan.pdf', {
      pages_read: 2,
      pages_failed: 1,
      failures: [{ page: 3, reason: 'no text found', ocr_error: 'tesseract crashed' }],
    }),
    doc('c', 'good.pdf', {}),
  ]
  api.on('GET', '/api/collections', (route) =>
    json([{ id: 1, name: 'default', documents }])(route),
  )
  api.on('DELETE', '/api/documents/c', async (route) => {
    documents = documents.filter((d) => d.id !== 'c')
    await route.fulfill({ status: 204 })
  })
  await page.goto('/')

  await expect(page.getByText('Reading: 1 of 3 pages')).toBeVisible()
  await expect(page.getByRole('progressbar', { name: 'Ingestion of reading.pdf' })).toHaveAttribute(
    'value',
    '1',
  )
  await expect(page.getByText('Partly read: 2 of 3 pages, 1 failed')).toBeVisible()
  await expect(page.getByLabel('Failed pages of scan.pdf')).toContainText(
    'Page 3: no text found (OCR: tesseract crashed)',
  )
  await expect(page.getByText('Ready: 3 pages')).toBeVisible()

  const messages: string[] = []
  page.once('dialog', (d) => {
    messages.push(d.message())
    void d.dismiss()
  })
  await page.getByRole('button', { name: 'Delete good.pdf' }).click()
  await expect.poll(() => messages).toEqual(['Delete good.pdf?'])
  await expect(page.getByText('good.pdf', { exact: true })).toBeVisible()
  expect(api.calls).not.toContain('DELETE /api/documents/c')

  page.once('dialog', (d) => {
    messages.push(d.message())
    void d.accept()
  })
  await page.getByRole('button', { name: 'Delete good.pdf' }).click()
  await expect(page.getByText('good.pdf', { exact: true })).toHaveCount(0)
  expect(api.calls).toContain('DELETE /api/documents/c')
})

test('uploads a PDF with a progress bar and then lists it', async ({ page, api }) => {
  let documents: ReturnType<typeof doc>[] = []
  let release!: () => void
  const gate = new Promise<void>((r) => (release = r))
  api.on('GET', '/api/collections', (route) =>
    json([{ id: 1, name: 'default', documents }])(route),
  )
  api.on('POST', '/api/collections/default/documents', async (route) => {
    await gate
    documents = [doc('n', 'sample.pdf', { state: 'queued', pages_read: 0 })]
    await route.fulfill({ status: 201, json: { id: 'n' } })
  })
  await page.goto('/')

  await page.locator('input[type=file]').setInputFiles(SAMPLE_PDF_PATH)
  await page.getByRole('button', { name: 'Upload' }).click()
  await expect(page.getByRole('progressbar', { name: 'Upload progress' })).toBeVisible()
  release()
  await expect(page.getByRole('progressbar', { name: 'Upload progress' })).toHaveCount(0)
  await expect(page.getByText('Queued: 0 of 3 pages')).toBeVisible()
})

test('shows the backend error when an upload is rejected', async ({ page, api }) => {
  api.on('GET', '/api/collections', json([]))
  api.on('POST', '/api/collections/default/documents', json({ detail: 'file is not a PDF' }, 422))
  await page.goto('/')

  await page.locator('input[type=file]').setInputFiles(SAMPLE_PDF_PATH)
  await page.getByRole('button', { name: 'Upload' }).click()
  await expect(page.getByRole('alert')).toHaveText('file is not a PDF')
})

test('polls a processing document until it is ready', async ({ page, api }) => {
  let polls = 0
  api.on('GET', '/api/collections', (route) => {
    polls += 1
    const s = polls < 3 ? { state: 'processing', pages_read: 1 } : {}
    return json([{ id: 1, name: 'default', documents: [doc('a', 'slow.pdf', s)] }])(route)
  })
  await page.goto('/')

  await expect(page.getByText('Reading: 1 of 3 pages')).toBeVisible()
  await expect(page.getByText('Ready: 3 pages')).toBeVisible({ timeout: 10_000 })
  await expect(page.getByRole('progressbar')).toHaveCount(0)
})

test('shows why a failed document failed', async ({ page, api }) => {
  api.on(
    'GET',
    '/api/collections',
    json([
      {
        id: 1,
        name: 'default',
        documents: [
          doc('a', 'broken.pdf', {
            state: 'failed',
            error: 'ingestion was interrupted',
            pages_read: 0,
          }),
        ],
      },
    ]),
  )
  await page.goto('/')

  await expect(page.getByRole('alert')).toHaveText('Failed: ingestion was interrupted')
  await expect(page.getByRole('progressbar')).toHaveCount(0)
  await expect(page.getByText(/Ready:/)).toHaveCount(0)
})

test('stops polling once every document is settled', async ({ page, api }) => {
  api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [doc('a', 'done.pdf', {})] }]))
  await page.goto('/')
  await expect(page.getByText('Ready: 3 pages')).toBeVisible()

  const count = () => api.calls.filter((c) => c === 'GET /api/collections').length
  const before = count()
  await page.waitForTimeout(5000) // more than two poll intervals
  expect(count()).toBe(before)
})

test('the empty state offers a sample PDF that uploads and appears in the list', async ({
  page,
  api,
}) => {
  let documents: ReturnType<typeof doc>[] = []
  api.on('GET', '/api/collections', (route) =>
    json([{ id: 1, name: 'default', documents }])(route),
  )
  api.on('POST', '/api/collections/default/documents', async (route) => {
    documents = [doc('s', 'sample.pdf', { state: 'queued', pages_read: 0 })]
    await route.fulfill({ status: 201, json: { id: 's' } })
  })
  await page.goto('/')

  await page.getByRole('button', { name: 'Try a sample PDF' }).click()
  await expect(page.getByText('sample.pdf', { exact: true })).toBeVisible()
  await expect(page.getByText('Queued: 0 of 3 pages')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Try a sample PDF' })).toHaveCount(0)
  expect(api.calls).toContain('POST /api/collections/default/documents')
})
