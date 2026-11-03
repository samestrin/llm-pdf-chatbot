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
})
