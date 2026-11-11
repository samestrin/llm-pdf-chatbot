import { fireEvent, render, screen, within } from '@testing-library/react'
import App from './App'

function stubFetch(response: () => Promise<Response>) {
  const fetch = vi.fn((url: string) =>
    url === '/api/collections' ? Promise.resolve(Response.json([])) : response(),
  )
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('App', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders the placeholder heading', () => {
    stubFetch(() => Promise.resolve(Response.json({ status: 'ok' })))
    render(<App />)
    expect(
      screen.getByRole('heading', { name: /llm pdf chatbot/i }),
    ).toBeInTheDocument()
  })

  it('has a skip link to the main landmark, a header and a documents sidebar', () => {
    stubFetch(() => Promise.resolve(Response.json({ status: 'ok' })))
    render(<App />)

    const skip = screen.getByRole('link', { name: 'Skip to chat' })
    const main = screen.getByRole('main')
    expect(skip).toHaveAttribute('href', `#${main.id}`)
    expect(main).toHaveAttribute('tabindex', '-1')
    // The skip link is the first thing a keyboard reaches.
    expect(document.body.querySelector('a, button, input, select, textarea')).toBe(skip)
    expect(within(screen.getByRole('banner')).getByRole('heading', { level: 1 })).toHaveTextContent(
      'LLM PDF Chatbot',
    )
    expect(screen.getByRole('complementary', { name: 'Documents' })).toBeInTheDocument()
    expect(within(main).getByRole('region', { name: 'Chat' })).toBeInTheDocument()
  })

  it('asks the backend for its health at same-origin /api/health, and says nothing when it is up', async () => {
    const fetch = stubFetch(() => Promise.resolve(Response.json({ status: 'ok' })))
    render(<App />)
    await vi.waitFor(() =>
      expect(fetch).toHaveBeenCalledWith('/api/health', expect.anything()),
    )
    await screen.findByRole('heading', { name: 'No documents yet' })
    expect(screen.queryByText(/Backend/)).toBeNull()
  })

  it('shows the backend as down on an error status', async () => {
    stubFetch(() => Promise.resolve(Response.json({ detail: 'down' }, { status: 503 })))
    render(<App />)
    expect(await screen.findByText('Backend is down')).toBeInTheDocument()
  })

  it('shows the backend as down when the request fails, with no Retry button', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    render(<App />)
    const banner = screen.getByRole('banner')
    expect(await within(banner).findByRole('status')).toHaveTextContent('Backend is down')
    expect(within(banner).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('tells the chat while documents load, and when none exist', async () => {
    let answer!: (r: Response) => void
    vi.stubGlobal('fetch', (url: string) =>
      url === '/api/collections'
        ? new Promise<Response>((r) => (answer = r))
        : Promise.resolve(Response.json({ status: 'ok' })),
    )
    render(<App />)
    const chat = screen.getByRole('region', { name: 'Chat' })
    expect(within(chat).getByText('Loading your documents…')).toBeInTheDocument()

    await vi.waitFor(() => expect(answer).toBeTypeOf('function'))
    answer(Response.json([{ id: 1, name: 'default', documents: [] }]))
    expect(
      await within(chat).findByRole('heading', { name: 'Nothing to ask about yet' }),
    ).toBeInTheDocument()
    expect(within(chat).queryByText('Loading your documents…')).toBeNull()
  })

  it('tells the chat which documents are still being read', async () => {
    const doc = (filename: string, state: string) => ({
      id: filename,
      filename,
      pages: 1,
      size_bytes: 1,
      status: { state, error: null, pages_total: 1, pages_read: 0, pages_failed: 0, failures: [] },
    })
    vi.stubGlobal('fetch', (url: string) =>
      Promise.resolve(
        url === '/api/collections'
          ? Response.json([
              { id: 1, name: 'default', documents: [doc('a.pdf', 'queued'), doc('b.pdf', 'ready')] },
            ])
          : Response.json({ status: 'ok' }),
      ),
    )
    render(<App />)
    const chat = screen.getByRole('region', { name: 'Chat' })
    expect(
      await within(chat).findByText('Still reading a.pdf. Answers leave it out until it is ready.'),
    ).toBeInTheDocument()
    expect(within(chat).queryByRole('heading', { name: 'Nothing to ask about yet' })).toBeNull()
  })

  it('stops showing the chat as loading when the documents fail to load', async () => {
    vi.stubGlobal('fetch', (url: string) =>
      Promise.resolve(
        url === '/api/collections'
          ? Response.json({ detail: 'down' }, { status: 503 })
          : Response.json({ status: 'ok' }),
      ),
    )
    render(<App />)
    await screen.findByText('Could not load documents: down')
    const chat = screen.getByRole('region', { name: 'Chat' })
    expect(within(chat).queryByText('Loading your documents…')).toBeNull()
    expect(chat).toHaveAttribute('aria-busy', 'false')
    // Whether there are documents is not known, so the chat does not say there are none.
    expect(within(chat).queryByRole('heading', { name: 'Nothing to ask about yet' })).toBeNull()
  })

  it('shows the chat first-run state once a retried load finds no documents', async () => {
    let calls = 0
    vi.stubGlobal('fetch', (url: string) => {
      if (url !== '/api/collections') return Promise.resolve(Response.json({ status: 'ok' }))
      calls += 1
      return Promise.resolve(
        calls === 1
          ? Response.json({ detail: 'down' }, { status: 503 })
          : Response.json([{ id: 1, name: 'default', documents: [] }]),
      )
    })
    render(<App />)
    const chat = screen.getByRole('region', { name: 'Chat' })
    await screen.findByText('Could not load documents: down')
    expect(within(chat).queryByRole('heading', { name: 'Nothing to ask about yet' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Retry loading documents' }))
    expect(
      await within(chat).findByRole('heading', { name: 'Nothing to ask about yet' }),
    ).toBeInTheDocument()
    expect(chat).toHaveAttribute('aria-busy', 'false')
    expect(within(chat).queryByText('Loading your documents…')).toBeNull()
  })

  describe('the active collection', () => {
    const doc = (id: string, filename: string) => ({
      id,
      filename,
      pages: 1,
      size_bytes: 1,
      status: { state: 'ready', error: null, pages_total: 1, pages_read: 1, pages_failed: 0, failures: [] },
    })
    const ndjson = (...events: object[]) =>
      new Response(events.map((e) => `${JSON.stringify(e)}\n`).join(''))

    it('is one control that drives both the upload and the question', async () => {
      let collections = [{ id: 1, name: 'default', documents: [doc('a', 'a.pdf')] }]
      const posts: string[] = []
      class FakeXhr {
        upload = {}
        status = 201
        responseText = '{}'
        onload: (() => void) | null = null
        open(_m: string, url: string) {
          posts.push(url)
        }
        send() {
          collections = [...collections, { id: 2, name: 'contracts', documents: [doc('b', 'lease.pdf')] }]
          this.onload?.()
        }
      }
      vi.stubGlobal('XMLHttpRequest', FakeXhr)
      vi.stubGlobal(
        'fetch',
        vi.fn((url: string) => {
          if (url === '/api/collections') return Promise.resolve(Response.json(collections))
          if (url.endsWith('/chat')) {
            posts.push(url)
            return Promise.resolve(ndjson({ type: 'done', mode: 'fast', model: 'm', cost: null }))
          }
          return Promise.resolve(Response.json({ status: 'ok' }))
        }),
      )
      render(<App />)
      await screen.findByText('a.pdf')

      // One collection control on the whole page, and no second one in the chat.
      expect(screen.getAllByRole('combobox')).toHaveLength(1)
      expect(screen.queryByLabelText('Ask in collection')).toBeNull()

      fireEvent.change(screen.getByLabelText('Collection'), { target: { value: 'new' } })
      fireEvent.change(screen.getByLabelText('New collection name'), {
        target: { value: 'contracts' },
      })
      const file = new File(['%PDF'], 'lease.pdf', { type: 'application/pdf' })
      fireEvent.change(screen.getByLabelText('PDF'), { target: { files: [file] } })
      await screen.findByText('lease.pdf')
      // The new name is now an existing collection, and still the active one.
      await vi.waitFor(() => expect(screen.getByLabelText('Collection')).toHaveDisplayValue('contracts'))
      expect(screen.queryByLabelText('New collection name')).toBeNull()

      fireEvent.change(screen.getByLabelText('Ask a question'), { target: { value: 'q' } })
      fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
      await vi.waitFor(() =>
        expect(posts).toEqual([
          '/api/collections/contracts/documents',
          '/api/collections/contracts/chat',
        ]),
      )
    })

    it('falls back to the first collection when the active one is removed', async () => {
      let collections = [
        { id: 1, name: 'alpha', documents: [doc('a', 'a.pdf')] },
        { id: 2, name: 'smoke', documents: [] as ReturnType<typeof doc>[] },
      ]
      vi.stubGlobal('confirm', () => true)
      vi.stubGlobal(
        'fetch',
        vi.fn((url: string, init?: RequestInit) => {
          if (url === '/api/collections/smoke' && init?.method === 'DELETE') {
            collections = collections.filter((c) => c.name !== 'smoke')
            return Promise.resolve(new Response(null, { status: 204 }))
          }
          if (url === '/api/collections') return Promise.resolve(Response.json(collections))
          return Promise.resolve(Response.json({ status: 'ok' }))
        }),
      )
      render(<App />)
      await screen.findByText('a.pdf')
      fireEvent.change(screen.getByLabelText('Collection'), { target: { value: 'name:smoke' } })

      fireEvent.click(await screen.findByRole('button', { name: 'Remove empty collection' }))

      await vi.waitFor(() => expect(screen.getByLabelText('Collection')).toHaveDisplayValue('alpha'))
      expect(screen.queryByRole('option', { name: 'smoke' })).toBeNull()
      expect(screen.queryByRole('button', { name: 'Remove empty collection' })).toBeNull()
    })
  })
})
