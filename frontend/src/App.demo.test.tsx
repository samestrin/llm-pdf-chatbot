import { fireEvent, render, screen, within } from '@testing-library/react'
import App from './App'
import sampleQuestions from '../public/samples/questions.json'
import { STORAGE_KEY } from './modelAccess'

// pdf.js needs a real canvas and worker; the viewer is opened, never drawn, here.
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: () => ({ promise: new Promise(() => {}), destroy: () => Promise.resolve() }),
  TextLayer: class {},
}))

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

const readyDoc = {
  id: 'd1',
  filename: 'lease.pdf',
  pages: 2,
  size_bytes: 100,
  status: {
    state: 'ready',
    error: null,
    pages_total: 2,
    pages_read: 2,
    pages_failed: 0,
    failures: [],
  },
}

const COLLECTIONS = [
  {
    id: 1,
    name: 'default',
    embedding_preset: 'openai',
    embedding_model: 'text-embedding-3-small',
    read_only: false,
    documents: [readyDoc],
  },
]

function ndjson(...events: object[]) {
  return new Response(events.map((e) => `${JSON.stringify(e)}\n`).join(''))
}

/** Every fetch the page makes, answered by URL; chat requests are recorded. */
function stubDemoFetch(options?: { chat?: () => Response; collections?: unknown[] }) {
  const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>((url) => {
    if (url === '/api/presets') return Promise.resolve(Response.json(PRESETS))
    if (url === '/api/health') return Promise.resolve(Response.json({ status: 'ok' }))
    if (url === '/api/collections')
      return Promise.resolve(Response.json(options?.collections ?? COLLECTIONS))
    if (url.endsWith('/chat'))
      return Promise.resolve(
        options?.chat?.() ?? ndjson({ type: 'done', mode: 'fast', model: 'm', cost: null }),
      )
    return Promise.resolve(Response.json({}))
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const chatCalls = (fetch: ReturnType<typeof stubDemoFetch>) =>
  fetch.mock.calls.filter(([url]) => url.endsWith('/chat'))

function ask(question: string) {
  fireEvent.change(screen.getByLabelText('Ask a question'), { target: { value: question } })
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
}

/** Open the key panel, pick a preset, type the secret and save. */
function saveKey(preset: string, secret: string, remember = false) {
  fireEvent.change(screen.getByLabelText('Provider'), { target: { value: preset } })
  const label = preset === 'demo' ? 'Demo code' : 'API key'
  fireEvent.change(screen.getByLabelText(label), { target: { value: secret } })
  if (remember) fireEvent.click(screen.getByLabelText('Remember on this device'))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
}

describe('App in a public demo', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
  })
  afterEach(() => vi.unstubAllGlobals())

  it('shows the public notice with the server TTL and the key panel control', async () => {
    stubDemoFetch()
    render(<App />)
    expect(
      await screen.findByText(
        'Public demo: do not upload private files. Uploads are deleted after 45 minutes.',
      ),
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('banner')).getByRole('button', { name: 'Add a key or demo code' }),
    ).toBeInTheDocument()
  })

  it('makes no model call without a key or code: asking opens the panel instead', async () => {
    const fetch = stubDemoFetch()
    render(<App />)
    await screen.findByText(/Public demo/)
    ask('How long is the lease?')
    expect(screen.getByRole('region', { name: 'Model key' })).toBeInTheDocument()
    expect(chatCalls(fetch)).toEqual([])
    // The question is kept, so it can be asked once a key is saved.
    expect(screen.getByLabelText('Ask a question')).toHaveValue('How long is the lease?')
  })

  it('sends the saved key only in the request headers of the chat call', async () => {
    const fetch = stubDemoFetch()
    render(<App />)
    fireEvent.click(
      await within(screen.getByRole('banner')).findByRole('button', {
        name: 'Add a key or demo code',
      }),
    )
    saveKey('openai', 'sk-visitor-key')
    ask('How long is the lease?')
    await vi.waitFor(() => expect(chatCalls(fetch)).toHaveLength(1))
    const [url, init] = chatCalls(fetch)[0] as [string, RequestInit]
    expect(url).toBe('/api/collections/default/chat')
    expect(init.headers).toEqual({
      'Content-Type': 'application/json',
      'X-Model-Preset': 'openai',
      'X-Model-Key': 'sk-visitor-key',
    })
    expect(url).not.toContain('sk-visitor-key')
    expect(String(init.body)).not.toContain('sk-visitor-key')
    // Session storage by default; local storage stays empty.
    expect(JSON.parse(sessionStorage.getItem(STORAGE_KEY)!).secret).toBe('sk-visitor-key')
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })

  it('remembers the key in local storage only when the box is ticked, and reloads it', async () => {
    stubDemoFetch()
    const { unmount } = render(<App />)
    fireEvent.click(
      await within(screen.getByRole('banner')).findByRole('button', {
        name: 'Add a key or demo code',
      }),
    )
    saveKey('openai', 'sk-visitor-key', true)
    expect(localStorage.getItem(STORAGE_KEY)).not.toBeNull()
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    unmount()
    const reloaded = stubDemoFetch()
    render(<App />)
    // The remembered key is used without opening the panel.
    expect(
      await screen.findByRole('button', { name: 'Model key: OpenAI' }),
    ).toBeInTheDocument()
    ask('again')
    await vi.waitFor(() => expect(chatCalls(reloaded)).toHaveLength(1))
  })

  it('clear wipes the key from both stores, and the next ask opens the panel again', async () => {
    const fetch = stubDemoFetch()
    render(<App />)
    fireEvent.click(
      await within(screen.getByRole('banner')).findByRole('button', {
        name: 'Add a key or demo code',
      }),
    )
    saveKey('openai', 'sk-visitor-key', true)
    fireEvent.click(screen.getByRole('button', { name: 'Model key: OpenAI' }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    ask('after clear')
    expect(screen.getByRole('region', { name: 'Model key' })).toBeInTheDocument()
    expect(chatCalls(fetch)).toEqual([])
  })

  it('sends a demo code as X-Demo-Code, never as X-Model-Key', async () => {
    const fetch = stubDemoFetch()
    render(<App />)
    fireEvent.click(
      await within(screen.getByRole('banner')).findByRole('button', {
        name: 'Add a key or demo code',
      }),
    )
    saveKey('demo', 'demo-code-123')
    ask('with a code')
    await vi.waitFor(() => expect(chatCalls(fetch)).toHaveLength(1))
    const [, init] = chatCalls(fetch)[0] as [string, RequestInit]
    expect(init.headers).toEqual({
      'Content-Type': 'application/json',
      'X-Model-Preset': 'demo',
      'X-Demo-Code': 'demo-code-123',
    })
  })

  it('shows the plain refusal when the collection was embedded with another preset', async () => {
    const message =
      'This collection was embedded with text-embedding-3-small; switch back to OpenAI to ask about it'
    stubDemoFetch({ chat: () => Response.json({ detail: message }, { status: 409 }) })
    render(<App />)
    fireEvent.click(
      await within(screen.getByRole('banner')).findByRole('button', {
        name: 'Add a key or demo code',
      }),
    )
    saveKey('synthetic', 'sk-other')
    ask('mismatched')
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
  })

  it('shows each collection’s embedding model', async () => {
    stubDemoFetch()
    render(<App />)
    const documents = await screen.findByRole('complementary', { name: 'Documents' })
    expect(
      await within(documents).findByText('Embedded with text-embedding-3-small'),
    ).toBeInTheDocument()
  })

  it('the upload control explains it needs a code or key, and opens the panel', async () => {
    const fetch = stubDemoFetch()
    render(<App />)
    const documents = await screen.findByRole('complementary', { name: 'Documents' })
    expect(
      within(documents).getByText('Uploading needs a demo code or your own key.'),
    ).toBeInTheDocument()
    expect(within(documents).getByLabelText('PDF', { selector: 'input' })).toBeDisabled()
    fireEvent.click(within(documents).getByRole('button', { name: 'Add a key or demo code' }))
    expect(screen.getByRole('region', { name: 'Model key' })).toBeInTheDocument()
    expect(fetch.mock.calls.filter(([url]) => url.includes('/documents'))).toEqual([])
  })

  it('a preset with no embedding model can upload and ask', async () => {
    const fetch = stubDemoFetch()
    render(<App />)
    fireEvent.click(
      await within(screen.getByRole('banner')).findByRole('button', {
        name: 'Add a key or demo code',
      }),
    )
    saveKey('synthetic', 'sk-syn')
    const documents = screen.getByRole('complementary', { name: 'Documents' })
    expect(within(documents).queryByText(/no embedding model/)).toBeNull()
    expect(within(documents).getByLabelText('PDF', { selector: 'input' })).toBeEnabled()
    ask('chat with synthetic')
    await vi.waitFor(() => expect(chatCalls(fetch)).toHaveLength(1))
  })

  it('the sample shows its suggested questions and no Delete, and opens in the viewer', async () => {
    const sample = {
      id: 1,
      name: 'default',
      embedding_preset: 'demo',
      embedding_model: 'demo-embedding',
      read_only: true,
      documents: [{ ...readyDoc, id: 's1', filename: 'sample.pdf' }],
    }
    const fetch = stubDemoFetch({ collections: [sample] })
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ preset: 'synthetic', secret: 'sk-syn', remember: true }),
    )
    render(<App />)

    const chips = await screen.findByRole('list', { name: 'Suggested questions' })
    expect(within(chips).getAllByRole('button').map((b) => b.textContent)).toEqual(
      sampleQuestions['sample.pdf'],
    )
    const documents = screen.getByRole('complementary', { name: 'Documents' })
    expect(within(documents).queryByRole('button', { name: 'Delete sample.pdf' })).toBeNull()

    fireEvent.click(within(chips).getAllByRole('button')[0])
    await vi.waitFor(() => expect(chatCalls(fetch)).toHaveLength(1))

    fireEvent.click(within(documents).getByRole('button', { name: 'View sample.pdf' }))
    expect(
      await screen.findByRole('dialog', { name: 'sample.pdf, page 1' }),
    ).toBeInTheDocument()
  })

  it('uploads carry the saved credentials as headers too', async () => {
    stubDemoFetch()
    const setRequestHeader = vi.fn()
    const send = vi.fn()
    class FakeXhr {
      upload = {}
      open = vi.fn()
      setRequestHeader = setRequestHeader
      send = send
      status = 201
      onload?: () => void
    }
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    render(<App />)
    fireEvent.click(
      await within(screen.getByRole('banner')).findByRole('button', {
        name: 'Add a key or demo code',
      }),
    )
    saveKey('openai', 'sk-visitor-key')
    const documents = screen.getByRole('complementary', { name: 'Documents' })
    const input = within(documents).getByLabelText('PDF', { selector: 'input' })
    const file = new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [file] } })
    await vi.waitFor(() => expect(send).toHaveBeenCalled())
    expect(setRequestHeader.mock.calls).toEqual([
      ['X-Model-Preset', 'openai'],
      ['X-Model-Key', 'sk-visitor-key'],
    ])
  })
})

describe('App outside a public demo', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('shows no notice, no key control and no panel, and asks without headers', async () => {
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>((url) => {
      if (url === '/api/presets')
        return Promise.resolve(
          Response.json({ public_demo: false, upload_ttl_minutes: null, presets: [] }),
        )
      if (url === '/api/collections') return Promise.resolve(Response.json(COLLECTIONS))
      if (url === '/api/health') return Promise.resolve(Response.json({ status: 'ok' }))
      return Promise.resolve(ndjson({ type: 'done', mode: 'fast', model: 'm', cost: null }))
    })
    vi.stubGlobal('fetch', fetch)
    render(<App />)
    await screen.findByText('Embedded with text-embedding-3-small')
    expect(screen.queryByText(/Public demo/)).toBeNull()
    expect(screen.queryByRole('button', { name: /key or demo code/ })).toBeNull()
    ask('no demo')
    await vi.waitFor(() =>
      expect(fetch.mock.calls.filter(([url]) => url.endsWith('/chat'))).toHaveLength(1),
    )
    const [, init] = fetch.mock.calls.find(([url]) => url.endsWith('/chat')) as [
      string,
      RequestInit,
    ]
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' })
  })
})