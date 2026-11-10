import { fireEvent, render, screen, within } from '@testing-library/react'
import Chat from './Chat'

function ndjson(...events: object[]) {
  return new Response(events.map((e) => `${JSON.stringify(e)}\n`).join(''))
}

const EMPTY_HINT =
  'Upload a PDF in the Documents panel, then ask a question about it here. Every answer cites the page it came from.'

function ask(question: string) {
  fireEvent.change(screen.getByLabelText('Ask a question'), { target: { value: question } })
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
}

describe('Chat', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('asks the default collection unless another is chosen', async () => {
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
      Promise.resolve(ndjson({ type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    vi.stubGlobal('fetch', fetch)
    render(<Chat collections={['other', 'default']} />)

    expect(screen.getByLabelText('Ask in collection')).toHaveValue('default')
    ask('one')
    expect(await screen.findByRole('article', { name: 'Answer' })).toHaveAttribute(
      'aria-busy',
      'false',
    )
    fireEvent.change(screen.getByLabelText('Ask in collection'), { target: { value: 'other' } })
    ask('two')

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(fetch.mock.calls.map((c) => c[0])).toEqual([
      '/api/collections/default/chat',
      '/api/collections/other/chat',
    ])
  })

  it('shows a refusal, and an error event as an alert', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        ndjson({ type: 'refusal', passages: [] }, { type: 'error', message: 'model failed' }),
      ),
    )
    render(<Chat collections={['default']} />)
    ask('anything')

    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(await within(answer).findByRole('alert')).toHaveTextContent('model failed')
    expect(answer).toHaveTextContent('The documents do not answer this.')
  })

  it('shows an answer that finishes empty as an error, with its cost', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(ndjson({ type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    render(<Chat collections={['default']} />)
    ask('anything')

    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(await within(answer).findByRole('alert')).toHaveTextContent(
      'The answer came back empty.',
    )
    expect(within(answer).getByLabelText('Answer details')).toHaveTextContent('cost unknown')
  })

  it('shows a request that fails before it streams as a failed answer', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')))
    render(<Chat collections={['default']} />)
    ask('anything')

    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(await within(answer).findByRole('alert')).toHaveTextContent(
      'The answer failed: Failed to fetch',
    )
  })

  it('stops a streaming answer when asked, and shows it as stopped', async () => {
    vi.stubGlobal('fetch', (_url: string, init?: RequestInit) => {
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          c.enqueue(new TextEncoder().encode(`${JSON.stringify({ type: 'refusal' })}\n`))
          init?.signal?.addEventListener('abort', () => c.error(new DOMException('', 'AbortError')))
        },
      })
      return Promise.resolve(new Response(body))
    })
    render(<Chat collections={['default']} />)
    ask('anything')

    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(await within(answer).findByText('No passage came close.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(await within(answer).findByRole('status')).toHaveTextContent(
      'You stopped this answer before it finished.',
    )
    expect(answer).toHaveAttribute('aria-busy', 'false')
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
  })

  it('asks in deep mode with the switch on, and lists each step as it arrives', async () => {
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
      Promise.resolve(
        ndjson(
          {
            type: 'step',
            question: 'How long is the lease?',
            pages: [
              { document: { id: 'd1', filename: 'lease.pdf' }, page: 3 },
              { document: { id: 'd2', filename: 'addendum.pdf' }, page: 1 },
            ],
          },
          { type: 'step', question: 'Is there parking?', pages: [] },
          { type: 'refusal', passages: [] },
          { type: 'done', mode: 'deep', model: 'r', cost: 0.5 },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetch)
    render(<Chat collections={['default']} />)

    const deep = screen.getByRole('switch', { name: 'Deep mode' })
    expect(screen.queryByText(/uses the reasoning model/)).toBeNull()
    fireEvent.click(deep)
    expect(screen.getByText(/uses the reasoning model/)).toBeInTheDocument()
    ask('compare')

    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(await within(answer).findByLabelText('Answer details')).toHaveTextContent(
      'deep · r · $0.5',
    )
    const steps = within(within(answer).getByRole('list', { name: 'Steps' })).getAllByRole(
      'listitem',
    )
    expect(steps.map((s) => s.textContent)).toEqual([
      'How long is the lease? lease.pdf, p. 3; addendum.pdf, p. 1',
      'Is there parking? no pages found',
    ])
    expect(fetch.mock.calls[0][1]?.body).toBe(
      JSON.stringify({ question: 'compare', mode: 'deep' }),
    )
  })

  it('cannot ask with no collection, and says what to do first', () => {
    render(<Chat collections={[]} empty />)
    fireEvent.change(screen.getByLabelText('Ask a question'), { target: { value: 'q' } })
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    expect(screen.getByRole('heading', { name: 'Nothing to ask about yet' })).toBeInTheDocument()
    expect(screen.getByText(EMPTY_HINT)).toBeInTheDocument()
  })

  it('does not claim there are no documents when it is not known', () => {
    render(<Chat collections={[]} />)
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    expect(screen.queryByRole('heading', { name: 'Nothing to ask about yet' })).toBeNull()
  })

  it('shows the first-run state when no collection holds a document, until the first question', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(ndjson({ type: 'refusal', passages: [] }, { type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    render(<Chat collections={['default']} empty />)
    expect(screen.getByRole('heading', { name: 'Nothing to ask about yet' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()

    ask('anything')
    await screen.findByRole('article', { name: 'Answer' })
    expect(screen.queryByRole('heading', { name: 'Nothing to ask about yet' })).toBeNull()
  })

  it('shows that documents are loading instead of the first-run state', () => {
    render(<Chat collections={[]} loading />)
    const chat = screen.getByRole('region', { name: 'Chat' })
    expect(within(chat).getByText('Loading your documents…')).toBeInTheDocument()
    expect(chat).toHaveAttribute('aria-busy', 'true')
    expect(screen.queryByRole('heading', { name: 'Nothing to ask about yet' })).toBeNull()
  })

  it('retries a failed answer in place, with the same question', async () => {
    const fetch = vi
      .fn<(url: string, init?: RequestInit) => Promise<Response>>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(
        ndjson(
          {
            type: 'claim',
            text: 'Two years.',
            document: { id: 'd1', filename: 'lease.pdf' },
            page: 3,
            chunk_id: 'd1-3',
            quote: 'two years',
          },
          { type: 'done', mode: 'fast', model: 'm', cost: null },
        ),
      )
    vi.stubGlobal('fetch', fetch)
    render(<Chat collections={['default']} />)
    ask('How long?')

    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(await within(answer).findByRole('alert')).toHaveTextContent(
      'The answer failed: Failed to fetch',
    )
    fireEvent.click(within(answer).getByRole('button', { name: 'Retry' }))

    expect(await within(answer).findByText('Two years.')).toBeInTheDocument()
    expect(screen.getAllByRole('article', { name: 'Answer' })).toHaveLength(1)
    expect(screen.getAllByRole('article', { name: 'Question' })).toHaveLength(1)
    expect(within(answer).queryByRole('alert')).toBeNull()
    expect(within(answer).queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(fetch.mock.calls.map((c) => c[1]?.body)).toEqual([
      JSON.stringify({ question: 'How long?', mode: 'fast' }),
      JSON.stringify({ question: 'How long?', mode: 'fast' }),
    ])
  })

  it('retries in the collection and mode the question was asked in', async () => {
    const fetch = vi
      .fn<(url: string, init?: RequestInit) => Promise<Response>>()
      .mockRejectedValue(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetch)
    render(<Chat collections={['default', 'other']} />)
    fireEvent.click(screen.getByRole('switch', { name: 'Deep mode' }))
    ask('How long?')
    const answer = await screen.findByRole('article', { name: 'Answer' })
    await within(answer).findByRole('alert')

    fireEvent.change(screen.getByLabelText('Ask in collection'), { target: { value: 'other' } })
    fireEvent.click(screen.getByRole('switch', { name: 'Deep mode' }))
    fireEvent.click(within(answer).getByRole('button', { name: 'Retry' }))

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(fetch.mock.calls[1][0]).toBe('/api/collections/default/chat')
    expect(fetch.mock.calls[1][1]?.body).toBe(JSON.stringify({ question: 'How long?', mode: 'deep' }))
  })

  it('offers no retry on an answer that was stopped or came back fine', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(ndjson({ type: 'refusal', passages: [] }, { type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    render(<Chat collections={['default']} />)
    ask('anything')
    const answer = await screen.findByRole('article', { name: 'Answer' })
    await within(answer).findByLabelText('Answer details')
    expect(within(answer).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('says which documents of the chosen collection are still being read', () => {
    render(
      <Chat
        collections={['default', 'other']}
        ingesting={{ default: ['lease.pdf', 'addendum.pdf'], other: ['x.pdf'] }}
      />,
    )
    expect(
      screen.getByText(
        'Still reading lease.pdf, addendum.pdf. Answers leave them out until they are ready.',
      ),
    ).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Ask in collection'), { target: { value: 'other' } })
    expect(
      screen.getByText('Still reading x.pdf. Answers leave it out until it is ready.'),
    ).toBeInTheDocument()
  })
})
