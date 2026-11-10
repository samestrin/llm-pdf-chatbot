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

  it('asks the backend for its health at same-origin /api/health', async () => {
    const fetch = stubFetch(() => Promise.resolve(Response.json({ status: 'ok' })))
    render(<App />)
    expect(await screen.findByText('Backend: ok')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/health', expect.anything())
  })

  it('shows the backend as unreachable on an error status', async () => {
    stubFetch(() => Promise.resolve(Response.json({ detail: 'down' }, { status: 503 })))
    render(<App />)
    expect(await screen.findByText('Backend: unreachable')).toBeInTheDocument()
  })

  it('shows the backend as unreachable when the request fails', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    render(<App />)
    expect(await screen.findByText('Backend: unreachable')).toBeInTheDocument()
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
})
