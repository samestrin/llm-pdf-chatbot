import { deleteDocument, listCollections, streamChat, uploadDocument, type ChatEvent } from './api'

class FakeXhr {
  static last: FakeXhr
  upload: { onprogress?: (e: unknown) => void } = {}
  onload?: () => void
  onerror?: () => void
  status = 0
  responseText = ''
  method = ''
  url = ''
  body: FormData | null = null
  constructor() {
    FakeXhr.last = this
  }
  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  send(body: FormData) {
    this.body = body
  }
}

describe('uploadDocument', () => {
  beforeEach(() => vi.stubGlobal('XMLHttpRequest', FakeXhr))
  afterEach(() => vi.unstubAllGlobals())

  const file = new File(['%PDF-'], 'a b.pdf', { type: 'application/pdf' })

  it('posts the file and reports progress fractions', async () => {
    const seen: number[] = []
    const done = uploadDocument('my docs', file, (f) => seen.push(f))
    const xhr = FakeXhr.last
    expect(xhr.method).toBe('POST')
    expect(xhr.url).toBe('/api/collections/my%20docs/documents')
    expect((xhr.body as FormData).get('file')).toBeInstanceOf(File)
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 4 })
    xhr.upload.onprogress?.({ lengthComputable: true, loaded: 4, total: 4 })
    xhr.status = 201
    xhr.onload?.()
    await done
    expect(seen).toEqual([0.25, 1])
  })

  it('rejects with the backend detail', async () => {
    const done = uploadDocument('c', file, () => {})
    const xhr = FakeXhr.last
    xhr.status = 413
    xhr.responseText = JSON.stringify({ detail: 'file is larger than 50 MB' })
    xhr.onload?.()
    await expect(done).rejects.toThrow('file is larger than 50 MB')
  })

  it('rejects when the backend is unreachable', async () => {
    const done = uploadDocument('c', file, () => {})
    FakeXhr.last.onerror?.()
    await expect(done).rejects.toThrow(/unreachable/)
  })
})

describe('uploadDocument progress guard', () => {
  beforeEach(() => vi.stubGlobal('XMLHttpRequest', FakeXhr))
  afterEach(() => vi.unstubAllGlobals())

  it('ignores progress events with no usable total', () => {
    const seen: number[] = []
    void uploadDocument('c', new File(['%PDF-'], 'a.pdf'), (f) => seen.push(f))
    const { onprogress } = FakeXhr.last.upload
    onprogress?.({ lengthComputable: false, loaded: 1, total: 0 })
    onprogress?.({ lengthComputable: true, loaded: 1, total: 0 })
    expect(seen).toEqual([])
  })
})

describe('listCollections and deleteDocument', () => {
  afterEach(() => vi.unstubAllGlobals())

  const stub = (res: Response) => vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(res)))

  it('returns the collections', async () => {
    stub(Response.json([{ id: 1, name: 'x', documents: [] }]))
    expect(await listCollections()).toEqual([{ id: 1, name: 'x', documents: [] }])
  })

  it('throws the JSON detail of an error', async () => {
    stub(Response.json({ detail: 'database unavailable' }, { status: 503 }))
    await expect(listCollections()).rejects.toThrow('database unavailable')
  })

  it('throws a generic message for a non-JSON error', async () => {
    stub(new Response('<html>', { status: 502 }))
    await expect(listCollections()).rejects.toThrow('request failed (502)')
  })

  it('deletes by id and surfaces failures', async () => {
    stub(new Response(null, { status: 204 }))
    await deleteDocument('a/b')
    expect(fetch).toHaveBeenCalledWith('/api/documents/a%2Fb', { method: 'DELETE' })
    stub(Response.json({ detail: 'document not found' }, { status: 404 }))
    await expect(deleteDocument('x')).rejects.toThrow('document not found')
  })
})

describe('streamChat', () => {
  afterEach(() => vi.unstubAllGlobals())

  function streamOf(...parts: string[]) {
    const enc = new TextEncoder()
    return new ReadableStream<Uint8Array>({
      start(c) {
        for (const p of parts) c.enqueue(enc.encode(p))
        c.close()
      },
    })
  }

  async function collect(gen: AsyncIterable<ChatEvent>) {
    const out: ChatEvent[] = []
    for await (const e of gen) out.push(e)
    return out
  }

  it('posts the question and yields NDJSON events, even split across chunks', async () => {
    const fetch = vi.fn(() =>
      Promise.resolve(
        new Response(
          streamOf(
            '{"type":"claim","text":"A","document":{"id":"d","filename":"f.pdf"},',
            '"page":2,"chunk_id":"c","quote":"q"}\n\n{"type":"done","mode":"fast",',
            '"model":"m","cost":null}',
          ),
        ),
      ),
    )
    vi.stubGlobal('fetch', fetch)

    const events = await collect(streamChat('my docs', 'why?'))

    expect(fetch).toHaveBeenCalledWith('/api/collections/my%20docs/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: 'why?', mode: 'fast' }),
      signal: undefined,
    })
    expect(events).toEqual([
      {
        type: 'claim',
        text: 'A',
        document: { id: 'd', filename: 'f.pdf' },
        page: 2,
        chunk_id: 'c',
        quote: 'q',
      },
      { type: 'done', mode: 'fast', model: 'm', cost: null },
    ])
  })

  it('throws the backend detail when the request is rejected', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(Response.json({ detail: 'bad mode' }, { status: 422 })))
    await expect(collect(streamChat('c', 'q'))).rejects.toThrow('bad mode')
  })

  it('throws on a line that is not an event', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(streamOf('not json\n'))))
    await expect(collect(streamChat('c', 'q'))).rejects.toThrow('malformed answer stream')
  })
})
