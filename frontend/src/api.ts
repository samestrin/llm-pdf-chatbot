// The backend is reached at same-origin `/api`; nginx and the Vite dev server
// proxy it (see nginx.conf and vite.config.ts).
export const API_BASE = '/api'

export type Health = 'ok' | 'unreachable'

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal })
    return res.ok ? 'ok' : 'unreachable'
  } catch (err) {
    if (signal?.aborted) throw err
    return 'unreachable'
  }
}

export interface PageFailure {
  page: number
  reason: string | null
  ocr_error: string | null
}

export interface DocumentStatus {
  state: 'queued' | 'processing' | 'ready' | 'failed'
  error: string | null
  pages_total: number
  pages_read: number
  pages_failed: number
  failures: PageFailure[]
}

export interface DocumentInfo {
  id: string
  filename: string
  pages: number
  size_bytes: number
  status: DocumentStatus
}

export interface Collection {
  id: number
  name: string
  documents: DocumentInfo[]
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // not JSON; fall through
  }
  return `request failed (${res.status})`
}

export async function listCollections(signal?: AbortSignal): Promise<Collection[]> {
  const res = await fetch(`${API_BASE}/collections`, { signal })
  if (!res.ok) throw new Error(await errorDetail(res))
  return res.json()
}

/** The stored PDF, served inline by the server-generated document id. */
export function documentPdfUrl(id: string): string {
  return `${API_BASE}/documents/${encodeURIComponent(id)}/pdf`
}

export async function deleteDocument(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/documents/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(await errorDetail(res))
}

/** Upload with XHR, because fetch cannot report upload progress. */
export function uploadDocument(
  collection: string,
  file: File,
  onProgress: (fraction: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/collections/${encodeURIComponent(collection)}/documents`)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve()
      let detail = `upload failed (${xhr.status})`
      try {
        const body = JSON.parse(xhr.responseText)
        if (typeof body.detail === 'string') detail = body.detail
      } catch {
        // keep the generic message
      }
      reject(new Error(detail))
    }
    xhr.onerror = () => reject(new Error('upload failed: backend unreachable'))
    const form = new FormData()
    form.append('file', file)
    xhr.send(form)
  })
}

// Chat stream contract (epic 04 plan, assumed from epic 03 T3/T6; check against
// the live backend when epic 03 closes): `POST /collections/{name}/chat` with
// `{question, mode}` answers with NDJSON, one event per line.
export interface CitedDocument {
  id: string
  filename: string
}

/** A rectangle on the page in PDF points, origin at the page's bottom-left. */
export interface QuoteBox {
  x0: number
  y0: number
  x1: number
  y1: number
}

export interface ClaimEvent {
  type: 'claim'
  text: string
  document: CitedDocument
  page: number
  chunk_id: string
  quote: string
  /** Where the quote is on the page, when the backend knows: drawn instead of a text search. */
  boxes?: QuoteBox[]
}

export interface Passage {
  document: CitedDocument
  page: number
  chunk_id: string
  text: string
}

export interface RefusalEvent {
  type: 'refusal'
  passages: Passage[]
}

export interface ErrorEvent {
  type: 'error'
  message: string
}

export interface DoneEvent {
  type: 'done'
  mode: string
  model: string
  cost: number | null
}

export type ChatEvent = ClaimEvent | RefusalEvent | ErrorEvent | DoneEvent

function parseEvent(line: string): ChatEvent {
  try {
    const event = JSON.parse(line)
    if (event && typeof event.type === 'string') return event
  } catch {
    // reported below
  }
  throw new Error('malformed answer stream')
}

/** Ask a question and yield the answer's events as they arrive. */
export async function* streamChat(
  collection: string,
  question: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${API_BASE}/collections/${encodeURIComponent(collection)}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, mode: 'fast' }),
    signal,
  })
  if (!res.ok) throw new Error(await errorDetail(res))
  if (!res.body) throw new Error('the answer has no body')

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) if (line.trim()) yield parseEvent(line)
  }
  if (buffer.trim()) yield parseEvent(buffer)
}
