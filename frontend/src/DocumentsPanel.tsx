import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  deleteDocument,
  listCollections,
  uploadDocument,
  type Collection,
  type DocumentInfo,
} from './api'

const POLL_MS = 2000
const DEFAULT_COLLECTION = 'default'

function isBusy(doc: DocumentInfo): boolean {
  return doc.status.state === 'queued' || doc.status.state === 'processing'
}

function StatusLine({ doc }: { doc: DocumentInfo }) {
  const s = doc.status
  if (s.state === 'failed') return <p role="alert">Failed: {s.error ?? 'unknown reason'}</p>
  if (isBusy(doc)) {
    return (
      <p>
        {s.state === 'queued' ? 'Queued' : 'Reading'}: {s.pages_read} of {s.pages_total} pages{' '}
        <progress
          aria-label={`Ingestion of ${doc.filename}`}
          max={s.pages_total || 1}
          value={s.pages_read}
        />
      </p>
    )
  }
  if (s.pages_failed > 0) {
    return (
      <div>
        <p>
          Partly read: {s.pages_read} of {s.pages_total} pages, {s.pages_failed} failed
        </p>
        <ul aria-label={`Failed pages of ${doc.filename}`}>
          {s.failures.map((f) => (
            <li key={f.page}>
              Page {f.page}: {f.reason ?? 'unknown reason'}
              {f.ocr_error ? ` (OCR: ${f.ocr_error})` : ''}
            </li>
          ))}
        </ul>
      </div>
    )
  }
  return <p>Ready: {s.pages_total} pages</p>
}

export default function DocumentsPanel() {
  const [collections, setCollections] = useState<Collection[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [collection, setCollection] = useState(DEFAULT_COLLECTION)
  const [file, setFile] = useState<File | null>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      setCollections(await listCollections(signal))
      setLoadError(null)
    } catch (err) {
      if (signal?.aborted) return
      setLoadError(err instanceof Error ? err.message : 'could not load documents')
    }
  }, [])

  const busy = collections.some((c) => c.documents.some(isBusy))

  useEffect(() => {
    const controller = new AbortController()
    const first = setTimeout(() => void refresh(controller.signal), 0)
    return () => {
      clearTimeout(first)
      controller.abort()
    }
  }, [refresh])

  useEffect(() => {
    if (!busy) return
    const controller = new AbortController()
    const timer = setInterval(() => void refresh(controller.signal), POLL_MS)
    return () => {
      clearInterval(timer)
      controller.abort()
    }
  }, [busy, refresh])

  async function onUpload(e: FormEvent) {
    e.preventDefault()
    const name = collection.trim()
    if (!file || !name) return
    setActionError(null)
    setProgress(0)
    try {
      await uploadDocument(name, file, setProgress)
      setFile(null)
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'upload failed')
    } finally {
      setProgress(null)
    }
  }

  async function onDelete(doc: DocumentInfo) {
    if (!window.confirm(`Delete ${doc.filename}?`)) return
    setActionError(null)
    try {
      await deleteDocument(doc.id)
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'delete failed')
    }
  }

  return (
    <section aria-labelledby="documents-heading">
      <h2 id="documents-heading">Documents</h2>
      <form onSubmit={onUpload}>
        <label>
          Collection{' '}
          <input value={collection} onChange={(e) => setCollection(e.target.value)} />
        </label>{' '}
        <label>
          PDF{' '}
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>{' '}
        <button type="submit" disabled={!file || !collection.trim() || progress !== null}>
          Upload
        </button>
        {progress !== null && (
          <p>
            Uploading {Math.round(progress * 100)}%{' '}
            <progress aria-label="Upload progress" max={1} value={progress} />
          </p>
        )}
      </form>
      {actionError && <p role="alert">{actionError}</p>}
      {loadError && <p role="alert">Could not load documents: {loadError}</p>}
      {collections.map((c) => (
        <div key={c.id}>
          <h3>{c.name}</h3>
          {c.documents.length === 0 && <p>No documents.</p>}
          <ul>
            {c.documents.map((doc) => (
              <li key={doc.id} aria-label={doc.filename}>
                <strong>{doc.filename}</strong> ({doc.pages} pages){' '}
                <button type="button" onClick={() => void onDelete(doc)}>
                  Delete {doc.filename}
                </button>
                <StatusLine doc={doc} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}
