import { fireEvent, render, screen, within } from '@testing-library/react'
import Chat, { pageRanges } from './Chat'

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

  it('asks in the active collection it is given, and has no collection control of its own', async () => {
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
      Promise.resolve(ndjson({ type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    vi.stubGlobal('fetch', fetch)
    const { rerender } = render(<Chat collection="default" />)

    expect(screen.queryByRole('combobox')).toBeNull()
    expect(screen.getByText('default', { selector: '.asking-in .filename' })).toBeInTheDocument()
    ask('one')
    expect(await screen.findByRole('article', { name: 'Answer' })).toHaveAttribute(
      'aria-busy',
      'false',
    )
    rerender(<Chat collection="other" />)
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
    render(<Chat collection="default" />)
    ask('anything')

    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(await within(answer).findByRole('alert')).toHaveTextContent('model failed')
    expect(answer).toHaveTextContent('The documents do not answer this.')
  })

  it('shows an answer that finishes empty as an error, with no unknown cost', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(ndjson({ type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    render(<Chat collection="default" />)
    ask('anything')

    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(await within(answer).findByRole('alert')).toHaveTextContent(
      'The answer came back empty.',
    )
    expect(within(answer).getByLabelText('Answer details')).toHaveTextContent('fast · m')
    expect(answer).not.toHaveTextContent('cost unknown')
  })

  it('shows a request that fails before it streams as a failed answer', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')))
    render(<Chat collection="default" />)
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
    render(<Chat collection="default" />)
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
    render(<Chat collection="default" />)

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
      'How long is the lease? 1 · p. 3; 2 · p. 1',
      'Is there parking? no pages found',
    ])
    expect(fetch.mock.calls[0][1]?.body).toBe(
      JSON.stringify({ question: 'compare', mode: 'deep' }),
    )
    // Two documents: the steps use numbered short citations and the names are listed once.
    expect(
      within(within(answer).getByRole('list', { name: 'Sources' })).getAllByRole('listitem').map(
        (li) => li.textContent,
      ),
    ).toEqual(['1 lease.pdf', '2 addendum.pdf'])
  })

  it('cannot ask with no collection, and says what to do first', () => {
    render(<Chat collection="" empty />)
    fireEvent.change(screen.getByLabelText('Ask a question'), { target: { value: 'q' } })
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    expect(screen.getByRole('heading', { name: 'Nothing to ask about yet' })).toBeInTheDocument()
    expect(screen.getByText(EMPTY_HINT)).toBeInTheDocument()
  })

  it('does not claim there are no documents when it is not known', () => {
    render(<Chat collection="" />)
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    expect(screen.queryByRole('heading', { name: 'Nothing to ask about yet' })).toBeNull()
  })

  it('shows the first-run state when no collection holds a document, until the first question', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(ndjson({ type: 'refusal', passages: [] }, { type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    render(<Chat collection="default" empty />)
    expect(screen.getByRole('heading', { name: 'Nothing to ask about yet' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()

    ask('anything')
    await screen.findByRole('article', { name: 'Answer' })
    expect(screen.queryByRole('heading', { name: 'Nothing to ask about yet' })).toBeNull()
  })

  it('shows that documents are loading instead of the first-run state', () => {
    render(<Chat collection="" loading />)
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
    render(<Chat collection="default" />)
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
    const { rerender } = render(<Chat collection="default" />)
    fireEvent.click(screen.getByRole('switch', { name: 'Deep mode' }))
    ask('How long?')
    const answer = await screen.findByRole('article', { name: 'Answer' })
    await within(answer).findByRole('alert')

    rerender(<Chat collection="other" />)
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
    render(<Chat collection="default" />)
    ask('anything')
    const answer = await screen.findByRole('article', { name: 'Answer' })
    await within(answer).findByLabelText('Answer details')
    expect(within(answer).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('says which documents of the chosen collection are still being read', () => {
    const ingesting = { default: ['lease.pdf', 'addendum.pdf'], other: ['x.pdf'] }
    const { rerender } = render(<Chat collection="default" ingesting={ingesting} />)
    expect(
      screen.getByText(
        'Still reading lease.pdf, addendum.pdf. Answers leave them out until they are ready.',
      ),
    ).toBeInTheDocument()
    rerender(<Chat collection="other" ingesting={ingesting} />)
    expect(
      screen.getByText('Still reading x.pdf. Answers leave it out until it is ready.'),
    ).toBeInTheDocument()
  })

  it('reads each answer out politely as it streams', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(ndjson({ type: 'refusal', passages: [] }, { type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    render(<Chat collection="default" />)
    ask('anything')
    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(answer).toHaveAttribute('aria-live', 'polite')
  })

  it('asks on Enter and starts a new line on Shift+Enter', async () => {
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
      Promise.resolve(ndjson({ type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    vi.stubGlobal('fetch', fetch)
    render(<Chat collection="default" />)
    const input = screen.getByLabelText('Ask a question')
    expect(input.tagName).toBe('TEXTAREA')
    fireEvent.change(input, { target: { value: 'first line' } })

    // Shift+Enter is left to the browser, which inserts the newline.
    expect(fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })).toBe(true)
    // Enter while an input method is composing a character does not ask either.
    expect(fireEvent.keyDown(input, { key: 'Enter', isComposing: true })).toBe(true)
    expect(fetch).not.toHaveBeenCalled()

    fireEvent.change(input, { target: { value: 'first line\nsecond line' } })
    expect(fireEvent.keyDown(input, { key: 'Enter' })).toBe(false)
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
    expect(fetch.mock.calls[0][1]?.body).toBe(
      JSON.stringify({ question: 'first line\nsecond line', mode: 'fast' }),
    )
    expect(input).toHaveValue('')
  })

  describe('citations and the lead line', () => {
    const LONG = `${'annual-financial-statement-and-auditor-report-'.repeat(3).slice(0, 96)}.pdf`
    const claim = (text: string, id: string, filename: string, page: number) => ({
      type: 'claim',
      text,
      document: { id, filename },
      page,
      chunk_id: `${id}-${page}`,
      quote: `quote for ${text}`,
    })
    const done = { type: 'done', mode: 'fast', model: 'm', cost: null }
    const answerWith = async (...events: object[]) => {
      vi.stubGlobal('fetch', () => Promise.resolve(ndjson(...events)))
      render(<Chat collection="default" />)
      ask('q')
      const answer = await screen.findByRole('article', { name: 'Answer' })
      await vi.waitFor(() => expect(answer).toHaveAttribute('aria-busy', 'false'))
      return answer
    }

    it('names one document once, with short page chips that keep the full name for screen readers', async () => {
      expect(LONG).toHaveLength(100)
      const answer = await answerWith(
        claim('One.', 'd1', LONG, 1),
        claim('Two.', 'd1', LONG, 2),
        claim('Three.', 'd1', LONG, 3),
        done,
      )

      expect(answer.textContent?.split(LONG)).toHaveLength(2)
      expect(within(answer).getByText(LONG)).toHaveAttribute('title', LONG)
      const chips = within(answer).getAllByRole('note', { name: 'Citation' })
      expect(chips.map((c) => c.textContent)).toEqual(['p. 1', 'p. 2', 'p. 3'])
      expect(chips[1]).toHaveAttribute('title', 'quote for Two.')
      expect(within(answer).getByRole('button', { name: `${LONG}, p. 2` })).toHaveTextContent(
        'p. 2',
      )
      expect(within(answer).getByText(/^Found in/).closest('p')).toHaveTextContent(
        `Found in ${LONG}, pp. 1–3`,
      )
      expect(within(answer).queryByRole('list', { name: 'Sources' })).toBeNull()

      fireEvent.click(within(answer).getByRole('button', { name: `${LONG}, p. 2` }))
      expect(
        await screen.findByText(/Opening the PDF viewer|Loading page|Page 2/),
      ).toBeInTheDocument()
    })

    it('numbers the sources of a two-document answer', async () => {
      const answer = await answerWith(
        claim('Two years.', 'd1', 'lease.pdf', 2),
        claim('One more.', 'd2', 'addendum.pdf', 1),
        claim('Rent is due monthly.', 'd1', 'lease.pdf', 4),
        done,
      )

      expect(
        within(answer)
          .getAllByRole('note', { name: 'Citation' })
          .map((c) => c.textContent),
      ).toEqual(['1 · p. 2', '2 · p. 1', '1 · p. 4'])
      expect(within(answer).getByRole('button', { name: 'addendum.pdf, p. 1' })).toBeInTheDocument()
      expect(
        within(within(answer).getByRole('list', { name: 'Sources' }))
          .getAllByRole('listitem')
          .map((li) => li.textContent),
      ).toEqual(['1 lease.pdf', '2 addendum.pdf'])
      expect(within(answer).getByText(/^Found in/)).toHaveTextContent(
        'Found in 2 documents, 3 points',
      )
    })

    it('never names a page no claim cites', async () => {
      const answer = await answerWith(
        claim('A.', 'd1', 'lease.pdf', 3),
        claim('B.', 'd1', 'lease.pdf', 1),
        done,
      )
      expect(within(answer).getByText(/^Found in/).closest('p')).toHaveTextContent(
        'Found in lease.pdf, pp. 1, 3',
      )
    })

    it('has no lead line on a refusal, an error or an empty answer', async () => {
      const refused = await answerWith(
        {
          type: 'refusal',
          passages: [{ document: { id: 'd1', filename: 'lease.pdf' }, page: 4, chunk_id: 'c', text: 'x' }],
        },
        done,
      )
      expect(within(refused).queryByText(/^Found in/)).toBeNull()
      expect(within(refused).getByRole('list', { name: 'Closest passages' })).toHaveTextContent(
        'p. 4',
      )
      expect(within(refused).getByText('lease.pdf')).toHaveAttribute('title', 'lease.pdf')
    })

    it('has no lead line when the answer fails after a claim', async () => {
      const failed = await answerWith(claim('Half.', 'd1', 'lease.pdf', 2), {
        type: 'error',
        message: 'x',
      })
      expect(within(failed).queryByText(/^Found in/)).toBeNull()
      // The document is still named once.
      expect(within(failed).getByText('lease.pdf')).toBeInTheDocument()
    })

    it('has no lead line on an empty answer', async () => {
      const empty = await answerWith(done)
      expect(within(empty).queryByText(/^Found in/)).toBeNull()
    })

    it('writes page ranges from the cited pages only', () => {
      expect(pageRanges([2])).toBe('p. 2')
      expect(pageRanges([3, 1, 2, 2])).toBe('pp. 1–3')
      expect(pageRanges([1, 3])).toBe('pp. 1, 3')
      expect(pageRanges([5, 1, 2, 7, 8])).toBe('pp. 1–2, 5, 7–8')
    })
  })
})
