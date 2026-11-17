import { useCallback, useEffect, useState } from 'react'
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
  clearCredentials,
  loadCredentials,
  saveCredentials,
  type ModelCredentials,
  type StoredCredentials,
} from './modelAccess'
import LogoMark from './LogoMark'

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
  const demo = presetList?.public_demo === true
  const preset = demo ? (presetList?.presets.find((p) => p.id === creds?.preset) ?? null) : null
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

  function onSaveKey(credentials: ModelCredentials, remember: boolean) {
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
          demo={
            demo && presetList
              ? {
                  ttlMinutes: presetList.upload_ttl_minutes,
                  credentials: creds,
                  preset,
                  onMissing: openPanel,
                }
              : undefined
          }
        />
        <main id="main" tabIndex={-1}>
          <Chat
            collection={active.name.trim()}
            loading={collections === null && !loadFailed}
            empty={collections !== null && collections.every((c) => c.documents.length === 0)}
            ingesting={ingesting}
            modelAccess={demo ? { credentials: creds, onMissing: openPanel } : undefined}
          />
        </main>
      </div>
    </div>
  )
}
