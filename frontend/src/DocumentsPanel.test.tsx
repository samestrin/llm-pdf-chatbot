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
