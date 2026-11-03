import { useCallback, useEffect, useState } from 'react'
import Chat from './Chat'
import ThemeToggle from './ThemeToggle'
import DocumentsPanel from './DocumentsPanel'
import { getHealth, type Collection, type Health } from './api'

export default function App() {
  const [health, setHealth] = useState<Health | 'checking'>('checking')
  const [collections, setCollections] = useState<string[]>([])
  const onCollections = useCallback(
    (loaded: Collection[]) => setCollections(loaded.map((c) => c.name)),
    [],
  )

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal).then(setHealth, () => {})
    return () => controller.abort()
  }, [])

  return (
    <main>
      <ThemeToggle />
      <h1>LLM PDF Chatbot</h1>
      <p role="status">Backend: {health}</p>
      <Chat collections={collections} />
      <DocumentsPanel onCollections={onCollections} />
    </main>
  )
}
