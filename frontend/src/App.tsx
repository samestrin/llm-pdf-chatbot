import { useCallback, useEffect, useState } from 'react'
import Chat from './Chat'
import ThemeToggle from './ThemeToggle'
import DocumentsPanel from './DocumentsPanel'
import { useActiveCollection } from './activeCollection'
import { getHealth, isIngesting, type Collection, type Health } from './api'
import LogoMark from './LogoMark'

export default function App() {
  const [health, setHealth] = useState<Health | 'checking'>('checking')
  // `null` until the documents panel first loads them.
  const [collections, setCollections] = useState<Collection[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const onLoadError = useCallback((message: string | null) => setLoadFailed(message !== null), [])
  const { active, setActive, onLoaded } = useActiveCollection()
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
    return () => controller.abort()
  }, [])

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
        <ThemeToggle />
      </header>
      <div className="app-layout">
        <DocumentsPanel
          active={active}
          onActiveChange={setActive}
          onCollections={onCollections}
          onLoadError={onLoadError}
        />
        <main id="main" tabIndex={-1}>
          <Chat
            collection={active.name.trim()}
            loading={collections === null && !loadFailed}
            empty={collections !== null && collections.every((c) => c.documents.length === 0)}
            ingesting={ingesting}
          />
        </main>
      </div>
    </div>
  )
}
