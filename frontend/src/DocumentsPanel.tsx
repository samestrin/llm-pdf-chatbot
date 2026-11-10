import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  deleteDocument,
  isIngesting,
  listCollections,
  uploadDocument,
  type Collection,
  type DocumentInfo,
} from './api'

const POLL_MS = 2000
const DEFAULT_COLLECTION = 'default'
const SAMPLE_URL = '/samples/sample.pdf'
const SAMPLE_NAME = 'sample.pdf'

function StatusLine({ doc }: { doc: DocumentInfo }) {
  const s = doc.status
  if (s.state === 'failed') return <p role="alert">Failed: {s.error ?? 'unknown reason'}</p>
  if (isIngesting(doc)) {
    return (
      <div>
        <p>
          {s.state === 'queued' ? 'Queued' : 'Reading'}: {s.pages_read} of {s.pages_total} pages{' '}
          <progress
            aria-label={`Ingestion of ${doc.filename}`}
            max={s.pages_total || 1}
            value={s.pages_read}
          />
        </p>
        <p className="ingesting-note">Still being read. You can ask about it once it is ready.</p>
      </div>
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

export default function DocumentsPanel({
  onCollections,
  onLoadError,
}: {
  /** Called with the collections each time they are loaded. */
  onCollections?: (collections: Collection[]) => void
  /** Called with the reason a load failed, and with `null` when one succeeds. */
  onLoadError?: (message: string | null) => void
} = {}) {
  const [collections, setCollections] = useState<Collection[]>([])
  // True until a load answers, and again while a retry is in flight.
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [collection, setCollection] = useState(DEFAULT_COLLECTION)
  const [file, setFile] = useState<File | null>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  // The upload that just failed, so it can be sent again as it was.
  const [failedUpload, setFailedUpload] = useState<{ name: string; file: File } | null>(null)

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const loaded = await listCollections(signal)
        setCollections(loaded)
        onCollections?.(loaded)
        setLoadError(null)
        onLoadError?.(null)
      } catch (err) {
        if (signal?.aborted) return
        const message = err instanceof Error ? err.message : 'could not load documents'
        setLoadError(message)
        onLoadError?.(message)
      }
      setLoading(false)
    },
    [onCollections, onLoadError],
  )

  const busy = collections.some((c) => c.documents.some(isIngesting))

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

  function onRetryLoad() {
    setLoadError(null)
    setLoading(true)
    void refresh()
  }

  async function upload(name: string, pdf: File) {
    setActionError(null)
    setFailedUpload(null)
    setProgress(0)
    try {
      await uploadDocument(name, pdf, setProgress)
      setFile(null)
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'upload failed')
      setFailedUpload({ name, file: pdf })
    } finally {
      setProgress(null)
    }
  }

  function onUpload(e: FormEvent) {
    e.preventDefault()
    const name = collection.trim()
    if (!file || !name) return
    void upload(name, file)
  }

  async function onSample() {
    const name = collection.trim()
    if (!name) return
    setActionError(null)
    setFailedUpload(null)
    setProgress(0)
    try {
      const res = await fetch(SAMPLE_URL)
      if (!res.ok) throw new Error(`could not load the sample PDF (${res.status})`)
      const blob = await res.blob()
      const sample = new File([blob], SAMPLE_NAME, { type: 'application/pdf' })
      await uploadDocument(name, sample, setProgress)
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
    setFailedUpload(null)
    try {
      await deleteDocument(doc.id)
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'delete failed')
    }
  }

  const firstRun =
    !loading && !loadError && collections.every((c) => c.documents.length === 0)

  return (
    <section
      aria-labelledby="documents-heading"
      aria-busy={loading}
      className="panel documents-panel"
    >
      <h2 id="documents-heading">Documents</h2>
      <form onSubmit={onUpload} className="upload-form">
        <label>
          Collection{' '}
          <input value={collection} onChange={(e) => setCollection(e.target.value)} />
        </label>
        <label>
          PDF{' '}
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
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
      {failedUpload && progress === null && (
        <button type="button" onClick={() => void upload(failedUpload.name, failedUpload.file)}>
          Retry upload
        </button>
      )}
      {loading && (
        <p className="loading">
          <span className="spinner" aria-hidden="true" /> Loading documents…
        </p>
      )}
      {loadError && (
        <>
          <p role="alert">Could not load documents: {loadError}</p>
          <button type="button" onClick={onRetryLoad}>
            Retry loading documents
          </button>
        </>
      )}
      {firstRun && (
        <div className="empty-state">
          <h3>No documents yet</h3>
          <p>Choose a PDF above and press Upload. Once it is read, ask about it in the chat.</p>
          <button
            type="button"
            onClick={() => void onSample()}
            disabled={!collection.trim() || progress !== null}
          >
            Try a sample PDF
          </button>
        </div>
      )}
      {!firstRun && collections.map((c) => (
        <div key={c.id}>
          <h3>{c.name}</h3>
          {c.documents.length === 0 && <p>No documents.</p>}
          <ul className="document-list">
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
