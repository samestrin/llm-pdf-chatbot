import { useEffect, useState } from 'react'
import DocumentsPanel from './DocumentsPanel'
import { getHealth, type Health } from './api'

export default function App() {
  const [health, setHealth] = useState<Health | 'checking'>('checking')

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal).then(setHealth, () => {})
    return () => controller.abort()
  }, [])

  return (
    <main>
      <h1>LLM PDF Chatbot</h1>
      <p>The chat interface is coming soon.</p>
      <p role="status">Backend: {health}</p>
      <DocumentsPanel />
    </main>
  )
}
