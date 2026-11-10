import { useCallback, useEffect, useId, useRef, useState, type DragEvent } from 'react'
import type { ActiveCollection } from './activeCollection'
import {
  deleteCollection,
  deleteDocument,
  isIngesting,
  listCollections,
  uploadDocument,
  type Collection,
  type DocumentInfo,
} from './api'

const POLL_MS = 2000
const SAMPLE_URL = '/samples/sample.pdf'
const SAMPLE_NAME = 'sample.pdf'
// Option values of the collection picker: an existing name, or a new collection.
const NEW_OPTION = 'new'
const nameOption = (name: string) => `name:${name}`

function isPdf(file: File): boolean {
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
}

function CollectionPicker({
  names,
  active,
  empty,
  disabled,
  onChange,
  onRemove,
}: {
  names: string[]
  active: ActiveCollection
  /** The active collection exists and holds no document. */
  empty: boolean
  disabled: boolean
  onChange: (next: ActiveCollection) => void
  onRemove: () => void
}) {
  const id = useId()
  // Before the first load (or with none yet) the active name is not in the list.
  const options = active.draft || names.includes(active.name) ? names : [active.name, ...names]
  return (
    <div className="collection-picker">
      {/* Labels by `for`, not by wrapping: a wrapped select or input adds its value to its name. */}
      <label htmlFor={`${id}-collection`}>Collection</label>
      <select
        id={`${id}-collection`}
        value={active.draft ? NEW_OPTION : nameOption(active.name)}
        disabled={disabled}
        onChange={(e) =>
          onChange(
            e.target.value === NEW_OPTION
              ? { name: '', draft: true }
              : { name: e.target.value.slice(nameOption('').length), draft: false },
          )
        }
      >
        {options.map((name) => (
          <option key={name} value={nameOption(name)}>
            {name}
          </option>
        ))}
        <option value={NEW_OPTION}>New collection…</option>
      </select>
      {active.draft && (
        <>
          <label htmlFor={`${id}-new`}>New collection name</label>
          <input
            id={`${id}-new`}
            value={active.name}
            maxLength={200}
            disabled={disabled}
            onChange={(e) => onChange({ name: e.target.value, draft: true })}
          />
        </>
      )}
      {empty && (
        <button type="button" className="secondary small" onClick={onRemove} disabled={disabled}>
          Remove empty collection
        </button>
      )}
    </div>
  )
}

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
  active,
  onActiveChange,
  onCollections,
  onLoadError,
}: {
  /** The active collection, owned by the page: uploads go into it and the chat asks in it. */
  active: ActiveCollection
  onActiveChange: (next: ActiveCollection) => void
  /** Called with the collections each time they are loaded. */
  onCollections?: (collections: Collection[]) => void
  /** Called with the reason a load failed, and with `null` when one succeeds. */
  onLoadError?: (message: string | null) => void
}) {
  const [collections, setCollections] = useState<Collection[]>([])
  // True until a load answers, and again while a retry is in flight.
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const collection = active.name.trim()
  const fileInput = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
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
      if (fileInput.current) fileInput.current.value = ''
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'upload failed')
      setFailedUpload({ name, file: pdf })
    } finally {
      setProgress(null)
    }
  }

  /** Upload a file picked with the button or dropped on the panel. */
  function take(file: File | undefined) {
    if (!file || progress !== null) return
    if (!isPdf(file)) {
      setFailedUpload(null)
      setActionError(`${file.name} is not a PDF. Only PDF files can be uploaded.`)
      return
    }
    if (!collection) {
      setFailedUpload(null)
      setActionError('Name the new collection first.')
      return
    }
    void upload(collection, file)
  }

  function onDragOver(e: DragEvent) {
    if (!e.dataTransfer.types.includes('Files')) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    setDragging(true)
  }

  function onDragLeave(e: DragEvent) {
    // Leaving for a child of the panel is not leaving the panel.
    if (e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget)) return
    setDragging(false)
  }

  function onDrop(e: DragEvent) {
    e.preventDefault()
    setDragging(false)
    take(e.dataTransfer.files[0])
  }

  async function onRemoveEmpty() {
    if (!window.confirm(`Remove the empty collection ${collection}?`)) return
    setActionError(null)
    setFailedUpload(null)
    try {
      await deleteCollection(collection)
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'could not remove the collection')
    }
  }

  async function onSample() {
    const name = collection
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
  const activeEmpty =
    !active.draft &&
    collections.some((c) => c.name === active.name && c.documents.length === 0)

  return (
    <aside
      aria-labelledby="documents-heading"
      aria-busy={loading}
      className={`panel documents-panel${dragging ? ' dragging' : ''}`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <h2 id="documents-heading">Documents</h2>
      <div className="upload-form">
        <CollectionPicker
          names={collections.map((c) => c.name)}
          active={active}
          empty={activeEmpty}
          disabled={progress !== null}
          onChange={onActiveChange}
          onRemove={() => void onRemoveEmpty()}
        />
        <div className="drop-zone">
          <p>{dragging ? 'Drop the PDF to upload it' : 'Drop a PDF here, or choose one.'}</p>
          <label>
            PDF{' '}
            <input
              ref={fileInput}
              type="file"
              accept="application/pdf"
              disabled={!collection || progress !== null}
              onChange={(e) => take(e.target.files?.[0])}
            />
          </label>
        </div>
        {progress !== null && (
          <p>
            Uploading {Math.round(progress * 100)}%{' '}
            <progress aria-label="Upload progress" max={1} value={progress} />
          </p>
        )}
      </div>
      {actionError && <p role="alert">{actionError}</p>}
      {failedUpload && progress === null && (
        <button
          type="button"
          className="secondary"
          onClick={() => void upload(failedUpload.name, failedUpload.file)}
        >
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
          <button type="button" className="secondary" onClick={onRetryLoad}>
            Retry loading documents
          </button>
        </>
      )}
      {firstRun && (
        <div className="empty-state">
          <h3>No documents yet</h3>
          <p>Drop a PDF on this panel or choose one above. Once it is read, ask about it in the chat.</p>
          <button
            type="button"
            onClick={() => void onSample()}
            disabled={!collection || progress !== null}
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
                <div className="document-row">
                  <strong className="filename" title={doc.filename}>
                    {doc.filename}
                  </strong>
                  <button
                    type="button"
                    className="delete-button"
                    aria-label={`Delete ${doc.filename}`}
                    onClick={() => void onDelete(doc)}
                  >
                    Delete
                  </button>
                </div>
                <StatusLine doc={doc} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </aside>
  )
}
