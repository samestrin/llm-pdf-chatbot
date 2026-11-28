import { fireEvent, render, screen, within } from '@testing-library/react'
import App, { copyrightYears } from './App'

// The only external URL allowed in the page (docs/demo-shell.md, Footer):
// the repo's LICENSE on GitHub. Everything else must be a relative reference.
const LICENSE_URL = 'https://github.com/samestrin/llm-pdf-chatbot/blob/main/LICENSE'

// A href is allowed when it is exactly the repo LICENSE URL, or a relative
// reference: no scheme and not protocol-relative (`//host/...`).
function isAllowedHref(href: string) {
  return (
    href === LICENSE_URL ||
    (!/^[a-z][a-z0-9+.-]*:/i.test(href) && !href.startsWith('//'))
  )
}

function stubFetch(response: () => Promise<Response>) {
  const fetch = vi.fn((url: string) =>
    url === '/api/collections' ? Promise.resolve(Response.json([])) : response(),
  )
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('copyrightYears', () => {
  it('shows one year when the start is the current year', () => {
    expect(copyrightYears(2025, 2025)).toBe('2025')
  })

  it('shows the range otherwise', () => {
    expect(copyrightYears(2025, 2026)).toBe('2025–2026')
  })
})

describe('isAllowedHref (the no-external-links check)', () => {
  it('allows exactly the repo LICENSE URL and relative references', () => {
    for (const href of [LICENSE_URL, '/', '/api/docs', '#chat', 'docs/api.md']) {
      expect(isAllowedHref(href)).toBe(true)
    }
  })

  it('refuses every other external URL', () => {
    for (const href of [
      'https://example.com/',
      // Near misses of the allowed URL are still refused.
      'http://github.com/samestrin/llm-pdf-chatbot/blob/main/LICENSE',
      `${LICENSE_URL}/`,
      `${LICENSE_URL}?plain=1`,
      'https://github.com/samestrin/llm-pdf-chatbot/blob/main/README.md',
      '//example.com/LICENSE',
      'mailto:sam@example.com',
    ]) {
      expect(isAllowedHref(href)).toBe(false)
    }
  })
})

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
    // The skip link jumps past the bar and the documents panel to the chat, in main.
    const target = document.getElementById(skip.getAttribute('href')!.slice(1))!
    expect(main).toContainElement(target)
    expect(target).toHaveAttribute('tabindex', '-1')
    expect(within(target).getByRole('region', { name: 'Chat' })).toBeInTheDocument()
    // The skip link is the first thing a keyboard reaches.
    expect(document.body.querySelector('a, button, input, select, textarea')).toBe(skip)
    // The page title and its tagline open main; the bar holds the app name and the nav.
    expect(within(main).getByRole('heading', { level: 1 })).toHaveTextContent('LLM PDF Chatbot')
    expect(within(main).getByText(/every answer cites the quote and page/)).toBeInTheDocument()
    expect(within(screen.getByRole('banner')).queryByRole('heading')).toBeNull()
    expect(screen.getByRole('complementary', { name: 'Documents' })).toBeInTheDocument()
    expect(within(main).getByRole('region', { name: 'Chat' })).toBeInTheDocument()
  })

  it('has a header bar with the logo and name linking home, and a Chat and API nav', () => {
    stubFetch(() => Promise.resolve(Response.json({ status: 'ok' })))
    render(<App />)

    const banner = screen.getByRole('banner')
    expect(within(banner).getByRole('link', { name: 'PDF Chat' })).toHaveAttribute('href', '/')
    const nav = within(banner).getByRole('navigation', { name: 'Main' })
    const links = within(nav).getAllByRole('link')
    expect(links.map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['Chat', '/'],
      ['API', '/api/docs'],
    ])
    expect(within(nav).getByRole('link', { name: 'Chat' })).toHaveAttribute('aria-current', 'page')
    expect(within(nav).getByRole('link', { name: 'API' })).not.toHaveAttribute('aria-current')
  })

  it('has the shell footer: the copyright line, MIT License and the API link', () => {
    stubFetch(() => Promise.resolve(Response.json({ status: 'ok' })))
    render(<App />)

    const footer = screen.getByRole('contentinfo')
    expect(footer).toHaveTextContent(
      `© 2025–${new Date().getFullYear()} Sam Estrin · MIT License`,
    )
    const nav = within(footer).getByRole('navigation', { name: 'Footer' })
    const links = within(nav).getAllByRole('link')
    // Only pages the app already serves: no Privacy or Terms page exists.
    expect(links.map((a) => [a.textContent, a.getAttribute('href')])).toEqual([['API', '/api/docs']])
    // MIT License is a plain link to the repo's LICENSE on GitHub; the
    // copyright text before it is unchanged (checked above).
    expect(within(footer).getByRole('link', { name: 'MIT License' })).toHaveAttribute(
      'href',
      LICENSE_URL,
    )
  })

  it('has no external links except the repo LICENSE on GitHub', () => {
    stubFetch(() => Promise.resolve(Response.json({ status: 'ok' })))
    render(<App />)

    const hrefs = Array.from(document.querySelectorAll('a[href]')).map((a) =>
      a.getAttribute('href'),
    )
    expect(hrefs.filter((href) => !isAllowedHref(href!))).toEqual([])
    // The audit is not vacuous: the one allowed external URL is present.
    const external = hrefs.filter((href) => /^[a-z][a-z0-9+.-]*:/i.test(href!) || href!.startsWith('//'))
    expect(external).toEqual([LICENSE_URL])
  })

  it('keeps the footer when the backend is down', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    render(<App />)
    await screen.findByText('Backend is down')
    expect(screen.getByRole('contentinfo')).toHaveTextContent('Sam Estrin')
    expect(
      within(screen.getByRole('contentinfo')).getByRole('link', { name: 'API' }),
    ).toHaveAttribute('href', '/api/docs')
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
    // The bar holds the name and nav only; the page says the backend is down in main.
    const main = screen.getByRole('main')
    expect(await within(main).findByRole('status')).toHaveTextContent('Backend is down')
    expect(within(main).queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(screen.getByRole('banner')).not.toHaveTextContent('Backend')
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
