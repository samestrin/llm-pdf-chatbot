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
  const [panelOpen, setPanelOpen] = useState(false)
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
  const preset = demo ? (presetList?.presets.find((p) => p.id === creds?.preset) ?? null) : null
  // Only the active preset's own key goes to a model call, never another preset's.
  const credentials = activeCredentials(creds)
  const openPanel = useCallback(() => setPanelOpen(true), [])
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
    saveCredentials(credentials, remember)
    setCreds({ ...credentials, remember })
    setPanelOpen(false)
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
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to chat
      </a>
      <header className="app-bar">
        <h1 className="app-title">
          <LogoMark />
          LLM PDF Chatbot
        </h1>
        {/* Only a backend that is down is worth a word; a healthy one says nothing. */}
        <div className="backend-status">
          <p role="status">{down ? 'Backend is down' : ''}</p>
        </div>
        {demo && (
          <button
            type="button"
            className="secondary small key-toggle"
            aria-expanded={panelOpen}
            onClick={() => setPanelOpen((open) => !open)}
          >
            {creds ? `Model key: ${preset?.label ?? creds.preset}` : 'Add a key or demo code'}
          </button>
        )}
        <ThemeToggle />
      </header>
      {demo && panelOpen && presetList && (
        <KeyPanel
          presets={presetList.presets}
          embeddingModel={presetList.embedding_model ?? null}
          stored={creds}
          onSave={onSaveKey}
          onClear={onClearKey}
          onClose={() => setPanelOpen(false)}
        />
      )}
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
              ? {
                  ttlMinutes: presetList.upload_ttl_minutes,
                  credentials,
                  onMissing: openPanel,
                }
              : undefined
          }
        />
        <main id="main" tabIndex={-1}>
          <Chat
            collection={activeName}
            loading={collections === null && !loadFailed}
            empty={collections !== null && collections.every((c) => c.documents.length === 0)}
            ingesting={ingesting}
            modelAccess={demo ? { credentials, onMissing: openPanel } : undefined}
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
        </main>
      </div>
    </div>
  )
}
