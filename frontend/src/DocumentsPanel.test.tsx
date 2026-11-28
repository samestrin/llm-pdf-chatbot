import { fireEvent, render, screen, within } from '@testing-library/react'
import { useActiveCollection } from './activeCollection'
import { uploadDocument, type Collection } from './api'
import DocumentsPanel from './DocumentsPanel'

/** The panel with the active collection held the way the page holds it. */
function Panel() {
  const { active, setActive, onLoaded } = useActiveCollection()
  return (
    <DocumentsPanel
      active={active}
      onActiveChange={setActive}
      onCollections={(loaded: Collection[]) => onLoaded(loaded.map((c) => c.name))}
    />
  )
}

// Upload goes through XHR. It is a spy on the real upload, so a test can script
// it with `mockResolvedValueOnce` and the like, or fake XHR itself.
vi.mock('./api', async (original) => {
  const api = await original<typeof import('./api')>()
  return { ...api, uploadDocument: vi.fn(api.uploadDocument) }
})

const busyDoc = {
  id: 'a',
  filename: 'slow.pdf',
  pages: 3,
  size_bytes: 1,
  status: {
    state: 'processing',
    error: null,
    pages_total: 3,
    pages_read: 1,
    pages_failed: 0,
    failures: [],
  },
}

const readyDoc = {
  ...busyDoc,
  id: 'b',
  filename: 'ready.pdf',
  status: { ...busyDoc.status, state: 'ready', pages_read: 3 },
}

describe('DocumentsPanel', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('does not report an aborted poll as an error', async () => {
    let listCalls = 0
    vi.stubGlobal('confirm', () => true)
    window.confirm = () => true
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: { method?: string; signal?: AbortSignal }) => {
        if (init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: 204 }))
        listCalls += 1
        if (listCalls === 1) {
          return Promise.resolve(Response.json([{ id: 1, name: 'c', documents: [busyDoc] }]))
        }
        if (listCalls === 2) {
          // The poll: stays in flight until its signal aborts.
          return new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener('abort', () =>
              reject(new DOMException('aborted', 'AbortError')),
            )
          })
        }
        // The refresh after delete: nothing is busy any more, so polling stops.
        return Promise.resolve(Response.json([{ id: 1, name: 'c', documents: [] }]))
      }),
    )
    render(<Panel />)
    await screen.findByText('slow.pdf')
    await vi.waitFor(() => expect(listCalls).toBe(2), { timeout: 5000 })

    fireEvent.click(screen.getByRole('button', { name: 'Delete slow.pdf' }))
    await screen.findByRole('heading', { name: 'No documents yet' })
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows that documents are loading until the first load answers', async () => {
    let answer!: (r: Response) => void
    vi.stubGlobal('fetch', () => new Promise<Response>((r) => (answer = r)))
    render(<Panel />)

    const panel = screen.getByRole('complementary', { name: 'Documents' })
    expect(within(panel).getByText('Loading documents…')).toBeInTheDocument()
    expect(panel).toHaveAttribute('aria-busy', 'true')
    expect(within(panel).queryByRole('heading', { name: 'No documents yet' })).toBeNull()

    await vi.waitFor(() => expect(answer).toBeTypeOf('function'))
    answer(Response.json([{ id: 1, name: 'c', documents: [readyDoc] }]))
    expect(await within(panel).findByText('ready.pdf')).toBeInTheDocument()
    expect(within(panel).queryByText('Loading documents…')).toBeNull()
    expect(panel).toHaveAttribute('aria-busy', 'false')
  })

  it.each([
    ['no collection', []],
    ['only empty collections', [{ id: 1, name: 'default', documents: [] }]],
  ])('first run with %s says what to do next', async (_name, body) => {
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json(body)))
    render(<Panel />)

    expect(await screen.findByRole('heading', { name: 'No documents yet' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'Drop a PDF on this panel or choose one above. Once it is read, ask about it in the chat.',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByText('No documents.')).toBeNull()
  })

  it('lists an empty collection left from before, and offers to remove it when active', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        Response.json([
          { id: 1, name: 'default', documents: [] },
          { id: 2, name: 'other', documents: [readyDoc] },
        ]),
      ),
    )
    render(<Panel />)

    expect(await screen.findByText('ready.pdf')).toBeInTheDocument()
    expect(screen.getByText('No documents.')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No documents yet' })).toBeNull()
    // `default` is the active collection, and empty.
    expect(screen.getByLabelText('Collection')).toHaveDisplayValue('default')
    expect(screen.getByRole('button', { name: 'Remove empty collection' })).toBeInTheDocument()
  })

  it('retries a failed load', async () => {
    let calls = 0
    vi.stubGlobal('fetch', () => {
      calls += 1
      return Promise.resolve(
        calls === 1
          ? Response.json({ detail: 'database is down' }, { status: 503 })
          : Response.json([{ id: 1, name: 'c', documents: [readyDoc] }]),
      )
    })
    render(<Panel />)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load documents: database is down',
    )
    expect(screen.queryByText('Loading documents…')).toBeNull()
    expect(screen.queryByRole('heading', { name: 'No documents yet' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry loading documents' }))

    expect(await screen.findByText('ready.pdf')).toBeInTheDocument()
    expect(calls).toBe(2)
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Retry loading documents' })).toBeNull()
  })

  it('retries a failed upload with the same file and collection', async () => {
    const upload = vi.mocked(uploadDocument)
    upload.mockReset()
    upload.mockRejectedValueOnce(new Error('upload failed: backend unreachable'))
    upload.mockResolvedValueOnce(undefined)
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json([])))
    render(<Panel />)
    await screen.findByRole('heading', { name: 'No documents yet' })

    const file = new File(['%PDF'], 'lease.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByLabelText('Collection'), { target: { value: 'new' } })
    fireEvent.change(screen.getByLabelText('New collection name'), {
      target: { value: 'contracts' },
    })
    // A chosen file uploads at once: there is no separate Upload button.
    expect(screen.queryByRole('button', { name: 'Upload' })).toBeNull()
    fireEvent.change(screen.getByLabelText('PDF'), { target: { files: [file] } })

    expect(await screen.findByRole('alert')).toHaveTextContent('upload failed: backend unreachable')
    fireEvent.click(screen.getByRole('button', { name: 'Retry upload' }))

    await vi.waitFor(() => expect(upload).toHaveBeenCalledTimes(2))
    expect(upload.mock.calls[1].slice(0, 2)).toEqual(['contracts', file])
    await vi.waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
    expect(screen.queryByRole('button', { name: 'Retry upload' })).toBeNull()
  })

  it('says one page, not 1 pages, for one-page documents', async () => {
    const one = (id: string, filename: string, state: string, read: number, failed: number) => ({
      ...busyDoc,
      id,
      filename,
      pages: 1,
      status: { ...busyDoc.status, state, pages_total: 1, pages_read: read, pages_failed: failed },
    })
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        Response.json([
          {
            id: 1,
            name: 'c',
            documents: [
              one('x', 'a.pdf', 'ready', 1, 0),
              one('y', 'b.pdf', 'processing', 0, 0),
              one('z', 'c.pdf', 'ready', 0, 1),
            ],
          },
        ]),
      ),
    )
    render(<Panel />)

    expect(await screen.findByRole('listitem', { name: 'a.pdf' })).toHaveTextContent('Ready: 1 page')
    expect(screen.getByRole('listitem', { name: 'a.pdf' }).textContent).not.toMatch(/1 pages/)
    expect(screen.getByRole('listitem', { name: 'b.pdf' })).toHaveTextContent('Reading: 0 of 1 page')
    expect(screen.getByRole('listitem', { name: 'b.pdf' }).textContent).not.toMatch(/1 pages/)
    expect(screen.getByRole('listitem', { name: 'c.pdf' }).textContent).not.toMatch(/1 pages/)
  })

  it('says a document that is still being read cannot be asked about yet', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(Response.json([{ id: 1, name: 'c', documents: [busyDoc, readyDoc] }])),
    )
    render(<Panel />)

    const busy = await screen.findByRole('listitem', { name: 'slow.pdf' })
    expect(busy).toHaveTextContent('Reading: 1 of 3 pages')
    expect(busy).toHaveTextContent('Still being read. You can ask about it once it is ready.')
    expect(screen.getByRole('listitem', { name: 'ready.pdf' })).not.toHaveTextContent(
      'Still being read',
    )
  })

  describe('sample PDF', () => {
    const uploads: string[] = []
    let docs: unknown[] = []
    class FakeXhr {
      upload = {} as { onprogress?: unknown }
      status = 201
      responseText = '{"id":"s"}'
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      open(_m: string, url: string) {
        uploads.push(url)
      }
      send(body: FormData) {
        uploads.push((body.get('file') as File).name)
        docs = [
          {
            ...busyDoc,
            id: 's',
            filename: 'sample.pdf',
            status: { ...busyDoc.status, state: 'ready', pages_read: 3 },
          },
        ]
        this.onload?.()
      }
    }
    const stubFetch = (sample: () => Promise<Response>) =>
      vi.stubGlobal(
        'fetch',
        vi.fn((url: string) =>
          url === '/samples/sample.pdf'
            ? sample()
            : Promise.resolve(Response.json([{ id: 1, name: 'default', documents: docs }])),
        ),
      )

    beforeEach(() => {
      uploads.length = 0
      docs = []
      vi.stubGlobal('XMLHttpRequest', FakeXhr)
    })

    it('uploads the sample into the selected collection from the empty state', async () => {
      stubFetch(() => Promise.resolve(new Response('%PDF-1.4', { status: 200 })))
      render(<Panel />)
      fireEvent.click(await screen.findByRole('button', { name: 'Try a sample PDF' }))
      await screen.findByText('Ready: 3 pages')
      expect(uploads).toEqual(['/api/collections/default/documents', 'sample.pdf'])
      expect(screen.queryByRole('button', { name: 'Try a sample PDF' })).toBeNull()
    })

    it('says so, and uploads nothing, when the sample is missing', async () => {
      stubFetch(() => Promise.resolve(new Response('nope', { status: 404 })))
      render(<Panel />)
      fireEvent.click(await screen.findByRole('button', { name: 'Try a sample PDF' }))
      expect((await screen.findByRole('alert')).textContent).toBe(
        'could not load the sample PDF (404)',
      )
      expect(uploads).toEqual([])
    })

    it('says so, and uploads nothing, when the sample cannot be fetched', async () => {
      stubFetch(() => Promise.reject(new Error('offline')))
      render(<Panel />)
      fireEvent.click(await screen.findByRole('button', { name: 'Try a sample PDF' }))
      expect((await screen.findByRole('alert')).textContent).toBe('offline')
      expect(uploads).toEqual([])
    })
  })

  describe('document rows', () => {
    const LONG = `${'board-minutes-and-annual-budget-review-'.repeat(4).slice(0, 116)}.pdf`

    it('truncates a long name with the whole name in a tooltip, and shows the page count once', async () => {
      expect(LONG).toHaveLength(120)
      vi.stubGlobal('fetch', () =>
        Promise.resolve(
          Response.json([
            { id: 1, name: 'default', documents: [{ ...readyDoc, filename: LONG }] },
          ]),
        ),
      )
      render(<Panel />)
      const row = await screen.findByRole('listitem', { name: LONG })

      const titled = row.querySelectorAll(`[title]`)
      expect([...titled].map((el) => el.getAttribute('title'))).toEqual([LONG])
      expect(titled[0]).toHaveClass('filename')
      const del = within(row).getByRole('button', { name: `Delete ${LONG}` })
      expect(del.textContent).toBe('Delete')
      expect(row.textContent?.match(/3 pages/g)).toEqual(['3 pages'])
      expect(row).toHaveTextContent('Ready: 3 pages')
      expect(row).not.toHaveTextContent('(3 pages)')
    })

    it('asks before it deletes', async () => {
      const confirm = vi.fn(() => false)
      vi.stubGlobal('confirm', confirm)
      const fetch = vi.fn(() =>
        Promise.resolve(Response.json([{ id: 1, name: 'default', documents: [readyDoc] }])),
      )
      vi.stubGlobal('fetch', fetch)
      render(<Panel />)
      fireEvent.click(await screen.findByRole('button', { name: 'Delete ready.pdf' }))
      expect(confirm).toHaveBeenCalledWith('Delete ready.pdf?')
      expect(fetch).toHaveBeenCalledTimes(1)
    })
  })

  describe('drop and pick', () => {
    beforeEach(() => {
      const upload = vi.mocked(uploadDocument)
      upload.mockReset()
      upload.mockResolvedValue(undefined)
      vi.stubGlobal('fetch', () =>
        Promise.resolve(Response.json([{ id: 1, name: 'papers', documents: [readyDoc] }])),
      )
    })

    const panel = () => screen.getByRole('complementary', { name: 'Documents' })
    const drop = (file: File) => {
      fireEvent.dragOver(panel(), { dataTransfer: { types: ['Files'], files: [file] } })
      fireEvent.drop(panel(), { dataTransfer: { types: ['Files'], files: [file] } })
    }

    it('uploads a dropped PDF into the active collection, with a highlight while dragging', async () => {
      render(<Panel />)
      await screen.findByText('ready.pdf')
      const file = new File(['%PDF'], 'dropped.pdf', { type: 'application/pdf' })

      fireEvent.dragOver(panel(), { dataTransfer: { types: ['Files'], files: [file] } })
      expect(panel()).toHaveClass('dragging')
      fireEvent.drop(panel(), { dataTransfer: { types: ['Files'], files: [file] } })
      expect(panel()).not.toHaveClass('dragging')

      await vi.waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(1))
      expect(vi.mocked(uploadDocument).mock.calls[0].slice(0, 2)).toEqual(['papers', file])
    })

    it('uploads nothing for a dropped file that is not a PDF, and says so', async () => {
      render(<Panel />)
      await screen.findByText('ready.pdf')
      drop(new File(['hello'], 'notes.txt', { type: 'text/plain' }))

      expect(await screen.findByRole('alert')).toHaveTextContent(
        'notes.txt is not a PDF. Only PDF files can be uploaded.',
      )
      expect(uploadDocument).not.toHaveBeenCalled()
    })

    it('uploads a picked file at once and clears the field after', async () => {
      render(<Panel />)
      await screen.findByText('ready.pdf')
      const input = screen.getByLabelText('PDF') as HTMLInputElement
      const file = new File(['%PDF'], 'picked.pdf', { type: 'application/pdf' })
      const cleared = vi.fn()
      Object.defineProperty(input, 'value', { set: cleared, get: () => '', configurable: true })

      fireEvent.change(input, { target: { files: [file] } })

      await vi.waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(1))
      expect(vi.mocked(uploadDocument).mock.calls[0].slice(0, 2)).toEqual(['papers', file])
      await vi.waitFor(() => expect(cleared).toHaveBeenCalledWith(''))
    })
  })

  describe('remove empty collection', () => {
    it('asks first, then removes the empty active collection', async () => {
      let collections = [{ id: 2, name: 'smoke', documents: [] as unknown[] }]
      const confirm = vi.fn(() => true)
      vi.stubGlobal('confirm', confirm)
      const fetch = vi.fn((url: string, init?: RequestInit) => {
        if (init?.method === 'DELETE') {
          collections = []
          return Promise.resolve(new Response(null, { status: 204 }))
        }
        return Promise.resolve(Response.json(collections))
      })
      vi.stubGlobal('fetch', fetch)
      render(<Panel />)

      fireEvent.click(await screen.findByRole('button', { name: 'Remove empty collection' }))

      expect(confirm).toHaveBeenCalledWith('Remove the empty collection smoke?')
      await vi.waitFor(() =>
        expect(fetch).toHaveBeenCalledWith('/api/collections/smoke', { method: 'DELETE' }),
      )
      await vi.waitFor(() =>
        expect(screen.queryByRole('button', { name: 'Remove empty collection' })).toBeNull(),
      )
      expect(screen.queryByRole('option', { name: 'smoke' })).toBeNull()
    })

    it('is not offered for a collection with documents', async () => {
      vi.stubGlobal('fetch', () =>
        Promise.resolve(Response.json([{ id: 1, name: 'default', documents: [readyDoc] }])),
      )
      render(<Panel />)
      await screen.findByText('ready.pdf')
      expect(screen.queryByRole('button', { name: 'Remove empty collection' })).toBeNull()
    })
  })
})

describe('DocumentsPanel in a public demo', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.unstubAllGlobals())

  function demoPanel(overrides?: {
    credentials?: { preset: string; secret: string } | null
    onMissing?: () => void
  }) {
    const onMissing = overrides?.onMissing ?? vi.fn()
    const demo = {
      credentials: overrides?.credentials === undefined ? null : overrides.credentials,
      onMissing,
    }
    function DemoPanel() {
      const { active, setActive, onLoaded } = useActiveCollection()
      return (
        <DocumentsPanel
          active={active}
          onActiveChange={setActive}
          onCollections={(loaded: Collection[]) => onLoaded(loaded.map((c) => c.name))}
          demo={demo}
        />
      )
    }
    render(<DemoPanel />)
    return { onMissing }
  }

  const stubCollections = (body: unknown[] = []) =>
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json(body))))

  it('leaves the public notice to the key panel: none in the sidebar', async () => {
    stubCollections()
    demoPanel()
    await screen.findByText('Uploading needs a demo code or your own key.')
    expect(screen.queryByText(/Public demo/)).toBeNull()
  })

  it('explains that uploading needs a code or key, and opens the panel instead of uploading', async () => {
    stubCollections()
    const { onMissing } = demoPanel()
    expect(
      await screen.findByText('Uploading needs a demo code or your own key.'),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('PDF', { selector: 'input' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Add a key or demo code' }))
    expect(onMissing).toHaveBeenCalledTimes(1)
    expect(uploadDocument).not.toHaveBeenCalled()
  })

  it('uploads with the credentials it is given', async () => {
    stubCollections()
    vi.mocked(uploadDocument).mockResolvedValue(undefined)
    demoPanel({ credentials: { preset: 'openai', secret: 'sk-k' } })
    const input = await screen.findByLabelText('PDF', { selector: 'input' })
    expect(input).not.toBeDisabled()
    const file = new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [file] } })
    await vi.waitFor(() =>
      expect(uploadDocument).toHaveBeenCalledWith('default', file, expect.any(Function), {
        preset: 'openai',
        secret: 'sk-k',
      }),
    )
  })

  it('uploads with a preset that has no embedding model: the server embeds', async () => {
    stubCollections()
    vi.mocked(uploadDocument).mockResolvedValue(undefined)
    demoPanel({ credentials: { preset: 'synthetic', secret: 'sk-s' } })
    const input = await screen.findByLabelText('PDF', { selector: 'input' })
    expect(input).not.toBeDisabled()
    expect(await screen.findByRole('button', { name: 'Try a sample PDF' })).toBeEnabled()
    expect(screen.queryByText(/no embedding model/)).toBeNull()
    const file = new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [file] } })
    await vi.waitFor(() =>
      expect(uploadDocument).toHaveBeenCalledWith('default', file, expect.any(Function), {
        preset: 'synthetic',
        secret: 'sk-s',
      }),
    )
  })

  it('the sample button opens the panel instead of uploading without a code or key', async () => {
    const fetchMock = vi.fn((url: string) =>
      url === '/samples/sample.pdf'
        ? Promise.resolve(new Response('%PDF-1.4', { status: 200 }))
        : Promise.resolve(Response.json([])),
    )
    vi.stubGlobal('fetch', fetchMock)
    vi.mocked(uploadDocument).mockResolvedValue(undefined)
    const { onMissing } = demoPanel()
    // The empty state offers the sample; the collection is named, so the button enables.
    fireEvent.click(await screen.findByRole('button', { name: 'Try a sample PDF' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Uploading needs a demo code or your own key.',
    )
    expect(onMissing).toHaveBeenCalledTimes(1)
    expect(uploadDocument).not.toHaveBeenCalled()
    // Not even the sample PDF is fetched: no upload work starts at all.
    expect(fetchMock.mock.calls.map(([url]) => url)).not.toContain('/samples/sample.pdf')
  })

  it('the sample upload carries the visitor’s credentials', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        url === '/samples/sample.pdf'
          ? Promise.resolve(new Response('%PDF-1.4', { status: 200 }))
          : Promise.resolve(Response.json([])),
      ),
    )
    vi.mocked(uploadDocument).mockResolvedValue(undefined)
    demoPanel({ credentials: { preset: 'openai', secret: 'sk-k' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Try a sample PDF' }))
    await vi.waitFor(() =>
      expect(uploadDocument).toHaveBeenCalledWith(
        'default',
        expect.objectContaining({ name: 'sample.pdf', type: 'application/pdf' }),
        expect.any(Function),
        { preset: 'openai', secret: 'sk-k' },
      ),
    )
  })

  it('shows each collection’s embedding model', async () => {
    stubCollections([
      {
        id: 1,
        name: 'default',
        embedding_preset: 'openai',
        embedding_model: 'text-embedding-3-small',
        documents: [readyDoc],
      },
    ])
    demoPanel({ credentials: { preset: 'openai', secret: 'sk-k' } })
    expect(
      await screen.findByText('Embedded with text-embedding-3-small'),
    ).toBeInTheDocument()
  })

  it('shows the backend’s plain refusal when an upload used another preset', async () => {
    stubCollections()
    const message =
      'This collection was embedded with text-embedding-3-small; switch back to OpenAI to ask about it'
    vi.mocked(uploadDocument).mockRejectedValue(new Error(message))
    demoPanel({ credentials: { preset: 'synthetic', secret: 'sk-s' } })
    const input = await screen.findByLabelText('PDF', { selector: 'input' })
    fireEvent.change(input, {
      target: { files: [new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' })] },
    })
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
  })
})

describe('DocumentsPanel retry in a public demo', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.unstubAllGlobals())

  it('a retry after the key was cleared opens the panel instead of uploading', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json([]))))
    vi.mocked(uploadDocument).mockRejectedValue(new Error('upload failed'))
    const onMissing = vi.fn()
    const credentials = { preset: 'openai', secret: 'sk-k' }
    const active = { name: 'default', draft: false }
    const noop = () => {}
    const { rerender } = render(
      <DocumentsPanel
        active={active}
        onActiveChange={noop}
        demo={{ credentials, onMissing }}
      />,
    )
    const input = await screen.findByLabelText('PDF', { selector: 'input' })
    fireEvent.change(input, {
      target: { files: [new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' })] },
    })
    const retry = await screen.findByRole('button', { name: 'Retry upload' })
    expect(uploadDocument).toHaveBeenCalledTimes(1)
    // The visitor clears the key, then retries the failed upload.
    rerender(
      <DocumentsPanel
        active={active}
        onActiveChange={noop}
        demo={{ credentials: null, onMissing }}
      />,
    )
    fireEvent.click(retry)
    expect(onMissing).toHaveBeenCalledTimes(1)
    expect(uploadDocument).toHaveBeenCalledTimes(1)
  })

  it('a retry after switching to a preset with no embedding model uploads again', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json([]))))
    const upload = vi.mocked(uploadDocument)
    upload.mockRejectedValueOnce(new Error('upload failed'))
    upload.mockResolvedValue(undefined)
    const onMissing = vi.fn()
    const active = { name: 'default', draft: false }
    const noop = () => {}
    const { rerender } = render(
      <DocumentsPanel
        active={active}
        onActiveChange={noop}
        demo={{ credentials: { preset: 'openai', secret: 'sk-k' }, onMissing }}
      />,
    )
    const input = await screen.findByLabelText('PDF', { selector: 'input' })
    fireEvent.change(input, {
      target: { files: [new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' })] },
    })
    const retry = await screen.findByRole('button', { name: 'Retry upload' })
    expect(upload).toHaveBeenCalledTimes(1)
    // The visitor switches to a preset with no embedding model, then retries.
    rerender(
      <DocumentsPanel
        active={active}
        onActiveChange={noop}
        demo={{ credentials: { preset: 'synthetic', secret: 'sk-s' }, onMissing }}
      />,
    )
    fireEvent.click(retry)
    await vi.waitFor(() => expect(upload).toHaveBeenCalledTimes(2))
    expect(upload.mock.calls[1][3]).toEqual({ preset: 'synthetic', secret: 'sk-s' })
    expect(onMissing).not.toHaveBeenCalled()
  })
})

describe('DocumentsPanel drop in a public demo', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => vi.unstubAllGlobals())

  it('a dropped PDF uploads with a preset that has no embedding model', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json([]))))
    vi.mocked(uploadDocument).mockResolvedValue(undefined)
    render(
      <DocumentsPanel
        active={{ name: 'default', draft: false }}
        onActiveChange={() => {}}
        demo={{
          credentials: { preset: 'synthetic', secret: 'sk-s' },
          onMissing: () => {},
        }}
      />,
    )
    const panel = await screen.findByRole('complementary', { name: 'Documents' })
    const file = new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' })
    fireEvent.drop(panel, { dataTransfer: { files: [file], types: ['Files'] } })
    await vi.waitFor(() =>
      expect(uploadDocument).toHaveBeenCalledWith('default', file, expect.any(Function), {
        preset: 'synthetic',
        secret: 'sk-s',
      }),
    )
  })
})

describe('DocumentsPanel and the read-only sample', () => {
  afterEach(() => vi.unstubAllGlobals())

  const listed = [
    { id: 1, name: 'default', read_only: true, documents: [{ ...readyDoc, id: 's', filename: 'sample.pdf' }] },
    { id: 2, name: 'mine', read_only: false, documents: [readyDoc] },
  ]

  it('shows no Delete on the sample, and Delete on the visitor’s own documents', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json(listed))))
    render(<Panel />)
    await screen.findByText('sample.pdf')
    expect(screen.queryByRole('button', { name: 'Delete sample.pdf' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Delete ready.pdf' })).toBeInTheDocument()
  })

  it('opens any document, the sample too, in the viewer from its name', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json(listed))))
    const onView = vi.fn()
    render(
      <DocumentsPanel
        active={{ name: 'default', draft: false }}
        onActiveChange={() => {}}
        onView={onView}
      />,
    )
    const open = await screen.findByRole('button', { name: 'View sample.pdf' })
    expect(open).toHaveTextContent('sample.pdf')
    fireEvent.click(open)
    expect(onView).toHaveBeenCalledWith(expect.objectContaining({ id: 's' }), open)
    fireEvent.click(screen.getByRole('button', { name: 'View ready.pdf' }))
    expect(onView).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 'b' }),
      screen.getByRole('button', { name: 'View ready.pdf' }),
    )
  })
})
