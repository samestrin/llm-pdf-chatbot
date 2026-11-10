import { fireEvent, render, screen } from '@testing-library/react'
import DocumentsPanel from './DocumentsPanel'

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
    render(<DocumentsPanel />)
    await screen.findByText('slow.pdf')
    await vi.waitFor(() => expect(listCalls).toBe(2), { timeout: 5000 })

    fireEvent.click(screen.getByRole('button', { name: 'Delete slow.pdf' }))
    await screen.findByText('No documents.')
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByRole('alert')).toBeNull()
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
      render(<DocumentsPanel />)
      fireEvent.click(await screen.findByRole('button', { name: 'Try a sample PDF' }))
      await screen.findByText('Ready: 3 pages')
      expect(uploads).toEqual(['/api/collections/default/documents', 'sample.pdf'])
      expect(screen.queryByRole('button', { name: 'Try a sample PDF' })).toBeNull()
    })

    it('says so, and uploads nothing, when the sample is missing', async () => {
      stubFetch(() => Promise.resolve(new Response('nope', { status: 404 })))
      render(<DocumentsPanel />)
      fireEvent.click(await screen.findByRole('button', { name: 'Try a sample PDF' }))
      expect((await screen.findByRole('alert')).textContent).toBe(
        'could not load the sample PDF (404)',
      )
      expect(uploads).toEqual([])
    })

    it('says so, and uploads nothing, when the sample cannot be fetched', async () => {
      stubFetch(() => Promise.reject(new Error('offline')))
      render(<DocumentsPanel />)
      fireEvent.click(await screen.findByRole('button', { name: 'Try a sample PDF' }))
      expect((await screen.findByRole('alert')).textContent).toBe('offline')
      expect(uploads).toEqual([])
    })
  })
})
