import AxeBuilder from '@axe-core/playwright'
import type { Route } from '@playwright/test'
import { SAMPLE_PDF_PATH, expect, json, test } from './fixtures'

// The public demo: every model call needs a demo code or the visitor's own key.
const PRESETS = {
  public_demo: true,
  upload_ttl_minutes: 45,
  presets: [
    {
      id: 'openai',
      label: 'OpenAI',
      kind: 'key',
      chat_model: 'gpt-5-nano',
      embedding_model: 'text-embedding-3-small',
    },
    {
      id: 'synthetic',
      label: 'Synthetic',
      kind: 'key',
      chat_model: 'hf:zai-org/GLM-5.3-Flash',
      embedding_model: null,
    },
    {
      id: 'demo',
      label: 'Demo code',
      kind: 'demo',
      chat_model: 'gpt-5-nano',
      embedding_model: 'text-embedding-3-small',
    },
  ],
}

const KEY = 'sk-e2e-visitor-key'
const STORAGE_KEY = 'pdfchat.model'

const collections = [
  {
    id: 1,
    name: 'default',
    embedding_preset: 'openai',
    embedding_model: 'text-embedding-3-small',
    read_only: false,
    documents: [
      {
        id: 'd1',
        filename: 'lease.pdf',
        pages: 2,
        size_bytes: 921,
        status: {
          state: 'ready',
          error: null,
          pages_total: 2,
          pages_read: 2,
          pages_failed: 0,
          failures: [],
        },
      },
    ],
  },
]

const answer = (route: Route) =>
  route.fulfill({
    headers: { 'Content-Type': 'application/x-ndjson' },
    body:
      [
        {
          type: 'claim',
          text: 'The lease runs for two years.',
          document: { id: 'd1', filename: 'lease.pdf' },
          page: 1,
          chunk_id: 'd1-1',
          quote: 'a term of two (2) years',
        },
        { type: 'done', mode: 'fast', model: 'fake-model', cost: null },
      ]
        .map((e) => JSON.stringify(e))
        .join('\n') + '\n',
  })

test.beforeEach(async ({ api }) => {
  api.on('GET', '/api/presets', json(PRESETS))
  api.on('GET', '/api/collections', json(collections))
})

async function ask(page: import('@playwright/test').Page, question: string) {
  await page.getByLabel('Ask a question').fill(question)
  await page.getByRole('button', { name: 'Ask' }).click()
}

const fieldLabel = (presetLabel: string) =>
  presetLabel === 'Demo code' ? 'Demo code' : `${presetLabel} API key`

/** Fill the open key panel; it saves as it is typed. */
async function saveKey(
  page: import('@playwright/test').Page,
  presetLabel: string,
  secret: string,
  remember = false,
) {
  await page.getByLabel('Provider').selectOption({ label: presetLabel })
  await page.getByLabel(fieldLabel(presetLabel)).fill(secret)
  if (remember) await page.getByLabel('Remember on this device').check()
}

const stored = (page: import('@playwright/test').Page) =>
  page.evaluate((key) => ({
    session: sessionStorage.getItem(key),
    local: localStorage.getItem(key),
  }), STORAGE_KEY)

const NOTICE = 'Public demo: do not upload private files. Uploads are deleted after 45 minutes.'

test('the key panel is open at the top, the notice its first line, and none in the sidebar', async ({
  page,
}) => {
  await page.goto('/')
  const panel = page.locator('section[aria-label="Model access"]')
  await expect(panel).toBeVisible()
  await expect(panel.locator(':scope > :first-child')).toHaveText(NOTICE)
  await expect(page.getByText(NOTICE)).toHaveCount(1)
  await expect(
    page.getByRole('complementary', { name: 'Documents' }).getByText(/Public demo/),
  ).toHaveCount(0)
  // No header key button and no dialog.
  await expect(page.getByRole('banner').getByRole('button', { name: /key|code/i })).toHaveCount(0)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  // Under the title block, above the documents and the chat.
  const title = await page.getByRole('heading', { level: 1 }).boundingBox()
  const box = await panel.boundingBox()
  const documents = await page.getByRole('complementary', { name: 'Documents' }).boundingBox()
  expect(box!.y).toBeGreaterThan(title!.y + title!.height - 1)
  expect(box!.y + box!.height).toBeLessThanOrEqual(documents!.y)
  await expect(page.getByText('Embedded with text-embedding-3-small')).toBeVisible()
})

/** The panel's controls, in DOM order, with their boxes. */
async function controls(page: import('@playwright/test').Page) {
  const panel = page.getByRole('region', { name: 'Model access' })
  const list = [
    panel.getByLabel('Provider'),
    panel.getByLabel('OpenAI API key'),
    panel.getByLabel('Remember on this device'),
    panel.getByRole('button', { name: 'Clear' }),
  ]
  return Promise.all(list.map(async (l) => (await l.boundingBox())!))
}

test('at 1280 px the controls sit in one row in the shell order', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/')
  const panel = page.getByRole('region', { name: 'Model access' })
  await expect(panel).toBeVisible()
  // Provider, key field, remember, clear: nothing else is a control in the panel.
  await expect(panel.locator('select, input, button')).toHaveCount(4)
  const [provider, field, remember, clear] = await controls(page)
  expect(provider.x + provider.width).toBeLessThanOrEqual(field.x)
  expect(field.x + field.width).toBeLessThanOrEqual(remember.x)
  expect(remember.x + remember.width).toBeLessThanOrEqual(clear.x)
  // One row: their bottoms line up (align-items: flex-end).
  const bottoms = [provider, field, clear].map((b) => Math.round(b.y + b.height))
  expect(Math.max(...bottoms) - Math.min(...bottoms)).toBeLessThanOrEqual(1)
  expect(provider.width).toBeGreaterThanOrEqual(220)
  expect(field.width).toBeGreaterThanOrEqual(260)
})

test('below 48rem the controls stack into one full-width column', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 })
  await page.goto('/')
  const panel = page.getByRole('region', { name: 'Model access' })
  await expect(panel).toBeVisible()
  const [provider, field, , clear] = await controls(page)
  expect(provider.y + provider.height).toBeLessThanOrEqual(field.y)
  expect(field.y + field.height).toBeLessThanOrEqual(clear.y)
  const row = (await panel.locator('.key-row').boundingBox())!
  for (const b of [provider, field, clear]) expect(Math.abs(b.width - row.width)).toBeLessThanOrEqual(1)
})

test('no model call without a key or code: asking goes to the key field instead', async ({
  page,
  api,
}) => {
  await page.goto('/')
  await ask(page, 'How long is the lease?')
  await expect(page.getByLabel('OpenAI API key')).toBeFocused()
  expect(api.calls).not.toContain('POST /api/collections/default/chat')
  // The question is kept for once a key is saved.
  await expect(page.getByLabel('Ask a question')).toHaveValue('How long is the lease?')
})

test('the key travels only in request headers, never in the URL or body', async ({
  page,
  api,
}) => {
  let headers: Record<string, string> = {}
  let url = ''
  let body = ''
  api.on('POST', '/api/collections/default/chat', (route) => {
    headers = route.request().headers()
    url = route.request().url()
    body = route.request().postData() ?? ''
    return answer(route)
  })
  await page.goto('/')
  await ask(page, 'How long is the lease?')
  await saveKey(page, 'OpenAI', KEY)
  await ask(page, 'How long is the lease?')
  await expect(page.getByRole('article', { name: 'Answer' })).toContainText(
    'The lease runs for two years.',
  )
  expect(headers['x-model-preset']).toBe('openai')
  expect(headers['x-model-key']).toBe(KEY)
  expect(url).not.toContain(KEY)
  expect(body).not.toContain(KEY)
  // Session storage by default; local storage stays empty.
  const stores = await stored(page)
  expect(JSON.parse(stores.session!)).toMatchObject({ preset: 'openai', keys: { openai: KEY } })
  expect(stores.local).toBeNull()
})

test('a demo code goes as X-Demo-Code, never as X-Model-Key', async ({ page, api }) => {
  let headers: Record<string, string> = {}
  api.on('POST', '/api/collections/default/chat', (route) => {
    headers = route.request().headers()
    return answer(route)
  })
  await page.goto('/')
  await ask(page, 'with a code')
  await saveKey(page, 'Demo code', 'demo-code-123')
  await ask(page, 'with a code')
  await expect(page.getByRole('article', { name: 'Answer' })).toBeVisible()
  expect(headers['x-model-preset']).toBe('demo')
  expect(headers['x-demo-code']).toBe('demo-code-123')
  expect(headers['x-model-key']).toBeUndefined()
})

test('remember keeps the key on the device; a reload uses it straight away', async ({
  page,
  api,
}) => {
  api.on('POST', '/api/collections/default/chat', answer)
  await page.goto('/')
  await ask(page, 'remembered')
  await saveKey(page, 'OpenAI', KEY, true)
  const stores = await stored(page)
  expect(stores.session).toBeNull()
  expect(JSON.parse(stores.local!)).toMatchObject({ keys: { openai: KEY }, remember: true })
  await page.reload()
  await expect(page.getByLabel('OpenAI API key')).toHaveValue(KEY)
  await expect(page.getByLabel('Remember on this device')).toBeChecked()
  await ask(page, 'after reload')
  await expect(page.getByRole('article', { name: 'Answer' })).toBeVisible()
})

for (const remember of [true, false]) {
  test(`switching provider and back restores the demo code (remember ${remember ? 'on' : 'off'})`, async ({
    page,
    api,
  }) => {
    let headers: Record<string, string> = {}
    api.on('POST', '/api/collections/default/chat', (route) => {
      headers = route.request().headers()
      return answer(route)
    })
    await page.goto('/')
    await saveKey(page, 'Demo code', 'demo-code-123', remember)
    // Switch to OpenAI: its field is empty, and typing its key keeps the demo code.
    await page.getByLabel('Provider').selectOption({ label: 'OpenAI' })
    await expect(page.getByLabel('OpenAI API key')).toHaveValue('')
    await saveKey(page, 'OpenAI', KEY)
    // Switch back to the demo: the code is restored, not forgotten.
    await page.getByLabel('Provider').selectOption({ label: 'Demo code' })
    await expect(page.getByLabel('Demo code')).toHaveValue('demo-code-123')
    const stores = await stored(page)
    const keep = remember ? stores.local : stores.session
    const drop = remember ? stores.session : stores.local
    expect(JSON.parse(keep!)).toMatchObject({
      preset: 'demo',
      keys: { demo: 'demo-code-123', openai: KEY },
      remember,
    })
    expect(drop).toBeNull()
    // The restored code travels only as X-Demo-Code, never as another preset's key.
    await ask(page, 'back on demo')
    await expect(page.getByRole('article', { name: 'Answer' })).toBeVisible()
    expect(headers['x-model-preset']).toBe('demo')
    expect(headers['x-demo-code']).toBe('demo-code-123')
    expect(headers['x-model-key']).toBeUndefined()
  })
}

test('clear wipes every stored key, not just the active preset’s', async ({ page }) => {
  await page.goto('/')
  await saveKey(page, 'Demo code', 'demo-code-123', true)
  await saveKey(page, 'OpenAI', KEY)
  await page.getByRole('button', { name: 'Clear' }).click()
  expect(await stored(page)).toEqual({ session: null, local: null })
  // No preset shows a key any more.
  await page.getByLabel('Provider').selectOption({ label: 'Demo code' })
  await expect(page.getByLabel('Demo code')).toHaveValue('')
})

test('clear wipes the key from both stores, and the next ask goes to the key field', async ({
  page,
  api,
}) => {
  await page.goto('/')
  await ask(page, 'before clear')
  await saveKey(page, 'OpenAI', KEY, true)
  await page.getByRole('button', { name: 'Clear' }).click()
  expect(await stored(page)).toEqual({ session: null, local: null })
  await ask(page, 'after clear')
  await expect(page.getByLabel('OpenAI API key')).toBeFocused()
  expect(api.calls).not.toContain('POST /api/collections/default/chat')
})

test('a mismatched preset shows the backend’s plain message', async ({ page, api }) => {
  const message =
    'This collection was embedded with text-embedding-3-small; switch back to OpenAI to ask about it'
  api.on('POST', '/api/collections/default/chat', json({ detail: message }, 409))
  await page.goto('/')
  await ask(page, 'mismatched')
  await saveKey(page, 'OpenAI', KEY)
  await ask(page, 'mismatched')
  await expect(page.getByRole('alert')).toContainText(message)
})

test('the upload control explains it needs a code or key, and goes to the key field', async ({
  page,
  api,
}) => {
  await page.goto('/')
  const documents = page.getByRole('complementary', { name: 'Documents' })
  await expect(documents.getByText('Uploading needs a demo code or your own key.')).toBeVisible()
  await expect(documents.getByLabel('PDF', { exact: true })).toBeDisabled()
  await documents.getByRole('button', { name: 'Add a key or demo code' }).click()
  await expect(page.getByLabel('OpenAI API key')).toBeFocused()
  expect(api.calls.filter((c) => c.startsWith('POST /api/collections'))).toEqual([])
})

test('on a phone the upload prompt scrolls back up to the whole key panel', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 640 })
  await page.goto('/')
  const panel = page.getByRole('region', { name: 'Model access' })
  const prompt = page
    .getByRole('complementary', { name: 'Documents' })
    .getByRole('button', { name: 'Add a key or demo code' })
  await prompt.scrollIntoViewIfNeeded()
  await expect(panel).not.toBeInViewport()
  await prompt.click()
  // The whole panel shows, its notice below the sticky header bar.
  await expect(async () => {
    const bar = (await page.getByRole('banner').boundingBox())!
    const box = (await panel.boundingBox())!
    expect(box.y).toBeGreaterThanOrEqual(bar.y + bar.height)
    expect(box.y + box.height).toBeLessThanOrEqual(640)
  }).toPass()
  await expect(page.getByLabel('OpenAI API key')).toBeFocused()
})

test('the sample PDF needs a code or key too: it goes to the key field, then the upload carries the headers', async ({
  page,
  api,
}) => {
  // An empty collection, so the empty state offers the sample button.
  api.on('GET', '/api/collections', json([{ id: 1, name: 'default', documents: [] }]))
  let headers: Record<string, string> = {}
  api.on('POST', '/api/collections/default/documents', (route) => {
    headers = route.request().headers()
    return route.fulfill({ status: 201, json: { id: 's' } })
  })
  await page.goto('/')
  const documents = page.getByRole('complementary', { name: 'Documents' })
  await documents.getByRole('button', { name: 'Try a sample PDF' }).click()
  await expect(page.getByLabel('OpenAI API key')).toBeFocused()
  await expect(documents.getByRole('alert')).toHaveText(
    'Uploading needs a demo code or your own key.',
  )
  expect(api.calls.filter((c) => c.startsWith('POST /api/collections'))).toEqual([])
  // With a key saved, the same click uploads, and the key travels only in headers.
  await saveKey(page, 'OpenAI', KEY)
  await documents.getByRole('button', { name: 'Try a sample PDF' }).click()
  await expect.poll(() => headers['x-model-key']).toBe(KEY)
  expect(headers['x-model-preset']).toBe('openai')
})

test('a preset with no embedding model can upload: the server embeds', async ({
  page,
  api,
}) => {
  const headers: Record<string, string> = {}
  api.on('POST', '/api/collections/default/documents', async (route) => {
    Object.assign(headers, route.request().headers())
    await route.fulfill({ status: 201, json: { id: 'n1', status: 'queued' } })
  })
  await page.goto('/')
  await saveKey(page, 'Synthetic', 'sk-synthetic')
  const documents = page.getByRole('complementary', { name: 'Documents' })
  await expect(documents.getByText(/no embedding model/)).toHaveCount(0)
  await documents.getByLabel('PDF', { exact: true }).setInputFiles(SAMPLE_PDF_PATH)
  await expect.poll(() => headers['x-model-preset']).toBe('synthetic')
})

test('the demo page with the key panel open has no serious accessibility violations', async ({
  page,
}) => {
  await page.goto('/')
  await expect(page.getByRole('region', { name: 'Model access' })).toBeVisible()
  const { violations } = await new AxeBuilder({ page }).analyze()
  const serious = violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
  expect(serious).toEqual([])
})