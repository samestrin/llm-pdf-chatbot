import { useCallback, useEffect, useState } from 'react'
import Chat from './Chat'
import ThemeToggle from './ThemeToggle'
import DocumentsPanel from './DocumentsPanel'
import { getHealth, isIngesting, type Collection, type Health } from './api'

export default function App() {
  const [health, setHealth] = useState<Health | 'checking'>('checking')
  // `null` until the documents panel first loads them.
  const [collections, setCollections] = useState<Collection[] | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const onLoadError = useCallback((message: string | null) => setLoadFailed(message !== null), [])

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

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to chat
      </a>
      <header className="app-bar">
        <h1>LLM PDF Chatbot</h1>
        <p role="status">Backend: {health}</p>
        <ThemeToggle />
      </header>
      <div className="app-layout">
        <DocumentsPanel onCollections={setCollections} onLoadError={onLoadError} />
        <main id="main" tabIndex={-1}>
          <Chat
            collections={(collections ?? []).map((c) => c.name)}
            loading={collections === null && !loadFailed}
            empty={collections !== null && collections.every((c) => c.documents.length === 0)}
            ingesting={ingesting}
          />
        </main>
      </div>
    </div>
  )
}
