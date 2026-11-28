import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import Chat from './Chat'
import ThemeToggle from './ThemeToggle'
import DocumentsPanel from './DocumentsPanel'
import KeyPanel from './KeyPanel'
import { useActiveCollection } from './activeCollection'
import {
  getHealth,
  getPresets,
  isIngesting,
  type Collection,
  type Health,
  type PresetList,
} from './api'
import {
  activeCredentials,
  clearCredentials,
  loadCredentials,
  saveCredentials,
  type PresetKeys,
  type StoredCredentials,
} from './modelAccess'
import LogoMark from './LogoMark'
import type { Viewing } from './PdfViewer'
import sampleQuestions from '../public/samples/questions.json'

// pdf.js is large; load the viewer the first time a document or citation is opened.
const PdfViewer = lazy(() => import('./PdfViewer'))
// Questions each sample PDF answers, by its file name (public/samples/questions.json).
const SAMPLE_QUESTIONS: Record<string, string[]> = sampleQuestions

/** The suggested questions of the sample PDFs in a collection, once each. */
export function suggestionsFor(collection: Collection | undefined): string[] {
  const names = new Set(collection?.documents.map((d) => d.filename))
  return [...new Set([...names].flatMap((name) => SAMPLE_QUESTIONS[name] ?? []))]
}

/** The copyright years: one year until the start year is past, then a range. */
export function copyrightYears(startYear: number, currentYear: number): string {
  return startYear === currentYear ? `${startYear}` : `${startYear}–${currentYear}`
}

/** The public demo's notice, the key panel's first line: uploads last `ttlMinutes`. */
export function publicNotice(ttlMinutes: number | null): string {
  return `Public demo: do not upload private files. ${
    ttlMinutes !== null
      ? `Uploads are deleted after ${ttlMinutes} minutes.`
      : 'Uploads are deleted automatically.'
  }`
}

export default function App() {
  const [health, setHealth] = useState<Health | 'checking'>('checking')
  // `null` until the documents panel first loads them.
  const [collections, setCollections] = useState<Collection[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const onLoadError = useCallback((message: string | null) => setLoadFailed(message !== null), [])
  const { active, setActive, onLoaded } = useActiveCollection()
  // The public demo's presets and notice, from GET /presets; null when the backend
  // does not answer it (then the page works as it always did, without credentials).
  const [presetList, setPresetList] = useState<PresetList | null>(null)
  const [creds, setCreds] = useState<StoredCredentials | null>(() => loadCredentials())
  // The key panel's field: with no key, asking or uploading sends the visitor there.
  const keyField = useRef<HTMLInputElement>(null)
  // The open PDF viewer, and the control that opened it, so closing puts focus back there.
  const [viewing, setViewing] = useState<Viewing | null>(null)
  const opener = useRef<HTMLElement | null>(null)
  const openViewer = useCallback((target: Viewing, from: HTMLElement) => {
    opener.current = from
    setViewing(target)
  }, [])
  const closeViewer = useCallback(() => {
    setViewing(null)
    opener.current?.focus()
  }, [])
  const demo = presetList?.public_demo === true
  // Only the active preset's own key goes to a model call, never another preset's.
  const credentials = activeCredentials(creds)
  // Scroll the whole open panel into view, its notice first, then focus the field.
  const toKeyField = useCallback(() => {
    const field = keyField.current
    field?.closest('.key-panel')?.scrollIntoView?.({ block: 'start' })
    field?.focus({ preventScroll: true })
  }, [])
  const onCollections = useCallback(
    (loaded: Collection[]) => {
      setCollections(loaded)
      onLoaded(loaded.map((c) => c.name))
    },
    [onLoaded],
  )

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal).then(setHealth, () => {})
    getPresets(controller.signal).then(setPresetList, () => {})
    return () => controller.abort()
  }, [])

  function onSaveKey(credentials: PresetKeys, remember: boolean) {
    // No key left to keep: nothing is stored, as after Clear.
    if (Object.keys(credentials.keys).length > 0) saveCredentials(credentials, remember)
    else clearCredentials()
    setCreds({ ...credentials, remember })
  }

  function onClearKey() {
    clearCredentials()
    setCreds(null)
  }

  const ingesting = Object.fromEntries(
    (collections ?? []).map((c) => [
      c.name,
      c.documents.filter(isIngesting).map((d) => d.filename),
    ]),
  )
  const down = health === 'unreachable'
  const activeName = active.name.trim()
  const suggestions = suggestionsFor(collections?.find((c) => c.name === activeName))

  return (
    <div className="app-shell">
      <a className="skip-link" href="#chat">
        Skip to chat
      </a>
      <header className="app-header">
        <div className="app-bar">
          <a className="app-title" href="/">
            <LogoMark />
            PDF Chat
          </a>
          <nav className="app-nav" aria-label="Main">
            <a href="/" aria-current="page">
              Chat
            </a>
            <a href="/api/docs">API</a>
          </nav>
          <div className="app-bar-actions">
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main id="main" className="app">
        <div className="app-content">
          <div className="app-heading">
            <h1>LLM PDF Chatbot</h1>
            <p className="tagline">
              Chat with your own PDFs and get answers you can check: every answer cites the quote
              and page it came from.
            </p>
          </div>
          {demo && presetList && (
            <KeyPanel
              presets={presetList.presets}
              notice={publicNotice(presetList.upload_ttl_minutes)}
              embeddingModel={presetList.embedding_model ?? null}
              stored={creds}
              onSave={onSaveKey}
              onClear={onClearKey}
              fieldRef={keyField}
            />
          )}
          {/* Only a backend that is down is worth a word; a healthy one says nothing. */}
          <p role="status" className="backend-status">
            {down ? 'Backend is down' : ''}
          </p>
          <div className="app-layout">
            <DocumentsPanel
              active={active}
              onActiveChange={setActive}
              onCollections={onCollections}
              onLoadError={onLoadError}
              onView={(doc, from) =>
                openViewer({ document: { id: doc.id, filename: doc.filename }, page: 1 }, from)
              }
              demo={
                demo && presetList
                  ? { credentials, onMissing: toKeyField }
                  : undefined
              }
            />
            <div id="chat" className="chat-column" tabIndex={-1}>
              <Chat
                collection={activeName}
                loading={collections === null && !loadFailed}
                empty={collections !== null && collections.every((c) => c.documents.length === 0)}
                ingesting={ingesting}
                modelAccess={demo ? { credentials, onMissing: toKeyField } : undefined}
                suggestions={suggestions}
                onOpen={openViewer}
                viewer={
                  viewing && (
                    <Suspense fallback={<p>Opening the PDF viewer…</p>}>
                      <PdfViewer
                        key={`${viewing.document.id}:${viewing.page}:${viewing.quote ?? ''}`}
                        citation={viewing}
                        onClose={closeViewer}
                      />
                    </Suspense>
                  )
                }
              />
            </div>
          </div>
        </div>
      </main>
      {/* The shell footer: copyright and licence left, the links row right. The
          licence name is text here; EST-9383 links it to the repo's LICENSE. */}
      <footer className="app-footer">
        <div className="app-footer-row">
          <p className="footer-copy">
            © {copyrightYears(2025, new Date().getFullYear())} Sam Estrin · MIT License
          </p>
          {/* Only pages the app already serves: the API reference. The spec adds
              no Privacy or Terms page, so those links are omitted. */}
          <nav aria-label="Footer">
            <ul className="footer-links">
              <li>
                <a href="/api/docs">API</a>
              </li>
            </ul>
          </nav>
        </div>
      </footer>
    </div>
  )
}
