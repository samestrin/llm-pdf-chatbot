import { readFileSync } from 'node:fs'
import type { Page } from '@playwright/test'
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

  // Choosing a file uploads it at once.
  await page.locator('input[type=file]').setInputFiles(SAMPLE_PDF_PATH)
  await expect(page.getByRole('progressbar', { name: 'Upload progress' })).toBeVisible()
  release()
  await expect(page.getByRole('progressbar', { name: 'Upload progress' })).toHaveCount(0)
  await expect(page.getByText('Queued: 0 of 3 pages')).toBeVisible()
})

test('shows the backend error when an upload is rejected', async ({ page, api }) => {
  api.on('GET', '/api/collections', json([]))
  api.on('POST', '/api/collections/default/documents', json({ detail: 'file is not a PDF' }, 422))
  await page.goto('/')

  // Choosing a file uploads it at once.
  await page.locator('input[type=file]').setInputFiles(SAMPLE_PDF_PATH)
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

/** A DataTransfer holding one file, as the browser builds it for a drag from the desktop. */
function dataTransfer(page: Page, name: string, type: string, bytes: Buffer) {
  return page.evaluateHandle(
    ({ name, type, b64 }) => {
      const dt = new DataTransfer()
      const data = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
      dt.items.add(new File([data], name, { type }))
      return dt
    },
    { name, type, b64: bytes.toString('base64') },
  )
}

test('a PDF dropped on the documents panel uploads into the active collection', async ({
  page,
  api,
}) => {
  let documents = [doc('a', 'first.pdf', {})]
  api.on('GET', '/api/collections', (route) =>
    json([{ id: 1, name: 'papers', documents }])(route),
  )
  api.on('POST', '/api/collections/papers/documents', async (route) => {
    documents = [...documents, doc('s', 'sample.pdf', { state: 'queued', pages_read: 0 })]
    await route.fulfill({ status: 201, json: { id: 's' } })
  })
  await page.goto('/')
  await expect(page.getByText('first.pdf', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Collection', { exact: true })).toHaveValue('name:papers')

  const panel = page.getByRole('complementary', { name: 'Documents' })
  const dt = await dataTransfer(page, 'sample.pdf', 'application/pdf', readFileSync(SAMPLE_PDF_PATH))
  await panel.dispatchEvent('dragover', { dataTransfer: dt })
  await expect(panel).toHaveClass(/dragging/)
  await panel.dispatchEvent('drop', { dataTransfer: dt })

  await expect(panel).not.toHaveClass(/dragging/)
  await expect(page.getByText('sample.pdf', { exact: true })).toBeVisible()
  expect(api.calls).toContain('POST /api/collections/papers/documents')
})

test('a dropped file that is not a PDF uploads nothing and says so', async ({ page, api }) => {
  api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [doc('a', 'a.pdf', {})] }]))
  await page.goto('/')
  await expect(page.getByText('a.pdf', { exact: true })).toBeVisible()

  const panel = page.getByRole('complementary', { name: 'Documents' })
  const dt = await dataTransfer(page, 'notes.txt', 'text/plain', Buffer.from('hello'))
  await panel.dispatchEvent('drop', { dataTransfer: dt })

  await expect(panel.getByRole('alert')).toHaveText(
    'notes.txt is not a PDF. Only PDF files can be uploaded.',
  )
  expect(api.calls.filter((c) => c.startsWith('POST'))).toEqual([])
})

test('a scanned PDF read with OCR off says it is a scan and OCR is off', async ({ page, api }) => {
  const reason = 'This PDF has no text layer (a scan or blank pages); OCR is off (set OCR_URL to read scans)'
  api.on(
    'GET',
    '/api/collections',
    json([
      {
        id: 1,
        name: 'default',
        documents: [doc('a', 'scan.pdf', { state: 'failed', error: reason, pages_read: 0 })],
      },
    ]),
  )
  await page.goto('/')

  await expect(page.getByRole('listitem', { name: 'scan.pdf' }).getByRole('alert')).toHaveText(
    `Failed: ${reason}`,
  )
})

test('a long file name stays on one line with an ellipsis, and Delete is small', async ({
  page,
  api,
}) => {
  const long = `${'annual-board-minutes-and-budget-review-'.repeat(4).slice(0, 116)}.pdf`
  api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [doc('a', long, {})] }]))
  await page.goto('/')

  const row = page.getByRole('listitem', { name: long })
  const name = row.locator('.filename')
  await expect(name).toHaveAttribute('title', long)
  const style = await name.evaluate((el) => {
    const s = getComputedStyle(el)
    return {
      overflow: s.textOverflow,
      wrap: s.whiteSpace,
      lines: Math.round(el.getBoundingClientRect().height / parseFloat(s.lineHeight)),
      cut: el.scrollWidth > el.clientWidth,
    }
  })
  expect(style).toEqual({ overflow: 'ellipsis', wrap: 'nowrap', lines: 1, cut: true })
  const del = row.getByRole('button', { name: `Delete ${long}` })
  await expect(del).toHaveText('Delete')
  await expect(row).toContainText('Ready: 3 pages')
  expect((await row.textContent())?.match(/3 pages/g)).toHaveLength(1)
})

test('deleting the last document drops its collection from the picker', async ({ page, api }) => {
  let collections = [
    { id: 1, name: 'alpha', documents: [doc('a', 'a.pdf', {})] },
    { id: 2, name: 'beta', documents: [doc('b', 'b.pdf', {})] },
  ]
  api.on('GET', '/api/collections', (route) => json(collections)(route))
  api.on('DELETE', '/api/documents/b', async (route) => {
    collections = collections.filter((c) => c.name !== 'beta')
    await route.fulfill({ status: 204 })
  })
  await page.goto('/')
  const picker = page.getByLabel('Collection', { exact: true })
  await picker.selectOption({ label: 'beta' })

  page.once('dialog', (d) => void d.accept())
  await page.getByRole('button', { name: 'Delete b.pdf' }).click()

  await expect(picker.getByRole('option', { name: 'beta' })).toHaveCount(0)
  await expect(picker).toHaveValue('name:alpha')
})

test('an empty collection left from before can be removed from the page', async ({
  page,
  api,
}) => {
  let collections = [
    { id: 1, name: 'default', documents: [doc('a', 'a.pdf', {})] },
    { id: 2, name: 'smoke', documents: [] as ReturnType<typeof doc>[] },
  ]
  api.on('GET', '/api/collections', (route) => json(collections)(route))
  api.on('DELETE', '/api/collections/smoke', async (route) => {
    collections = collections.filter((c) => c.name !== 'smoke')
    await route.fulfill({ status: 204 })
  })
  await page.goto('/')
  const picker = page.getByLabel('Collection', { exact: true })
  await expect(page.getByRole('button', { name: 'Remove empty collection' })).toHaveCount(0)
  await picker.selectOption({ label: 'smoke' })

  const messages: string[] = []
  page.once('dialog', (d) => {
    messages.push(d.message())
    void d.accept()
  })
  await page.getByRole('button', { name: 'Remove empty collection' }).click()

  await expect.poll(() => messages).toEqual(['Remove the empty collection smoke?'])
  await expect(picker.getByRole('option', { name: 'smoke' })).toHaveCount(0)
  await expect(picker).toHaveValue('name:default')
})

test('a new collection name takes the next upload and the next question', async ({
  page,
  api,
}) => {
  let collections = [{ id: 1, name: 'default', documents: [doc('a', 'a.pdf', {})] }]
  api.on('GET', '/api/collections', (route) => json(collections)(route))
  api.on('POST', '/api/collections/contracts/documents', async (route) => {
    collections = [...collections, { id: 2, name: 'contracts', documents: [doc('l', 'lease.pdf', {})] }]
    await route.fulfill({ status: 201, json: { id: 'l' } })
  })
  api.on('POST', '/api/collections/contracts/chat', (route) =>
    route.fulfill({
      headers: { 'Content-Type': 'application/x-ndjson' },
      body: `${JSON.stringify({ type: 'refusal', passages: [] })}\n${JSON.stringify({ type: 'done', mode: 'fast', model: 'm', cost: null })}\n`,
    }),
  )
  await page.goto('/')
  await expect(page.getByRole('combobox')).toHaveCount(1)

  await page.getByLabel('Collection', { exact: true }).selectOption({ label: 'New collection…' })
  await page.getByLabel('New collection name').fill('contracts')
  await page.locator('input[type=file]').setInputFiles(SAMPLE_PDF_PATH)
  await expect(page.getByText('lease.pdf', { exact: true })).toBeVisible()
  await expect(page.getByLabel('Collection', { exact: true })).toHaveValue('name:contracts')

  await page.getByLabel('Ask a question').fill('How long?')
  await page.getByRole('button', { name: 'Ask' }).click()
  await expect(page.getByRole('article', { name: 'Answer' })).toHaveAttribute('aria-busy', 'false')
  expect(api.calls).toContain('POST /api/collections/contracts/chat')
})
