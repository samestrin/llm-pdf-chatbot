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
