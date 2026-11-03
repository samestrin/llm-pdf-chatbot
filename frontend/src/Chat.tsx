import { Suspense, lazy, useEffect, useRef, useState, type FormEvent } from 'react'
import { streamChat, type ChatEvent, type ClaimEvent, type DoneEvent } from './api'

// pdf.js is large; load the viewer the first time a citation is opened.
const PdfViewer = lazy(() => import('./PdfViewer'))

const DEFAULT_COLLECTION = 'default'

interface Turn {
  question: string
  claims: ClaimEvent[]
  refused: boolean
  error: string | null
  done: DoneEvent | null
  streaming: boolean
}

function apply(turn: Turn, event: ChatEvent): Turn {
  switch (event.type) {
    case 'claim':
      return { ...turn, claims: [...turn.claims, event] }
    case 'refusal':
      return { ...turn, refused: true }
    case 'error':
      return { ...turn, error: event.message, streaming: false }
    case 'done':
      return { ...turn, done: event, streaming: false }
    default:
      return turn
  }
}

function Citation({ claim, onOpen }: { claim: ClaimEvent; onOpen: (c: ClaimEvent) => void }) {
  return (
    <span role="note" aria-label="Citation" className="citation-chip" title={claim.quote}>
      <button type="button" onClick={() => onOpen(claim)}>
        {claim.document.filename}, p. {claim.page}
      </button>
    </span>
  )
}

function Answer({ turn, onOpen }: { turn: Turn; onOpen: (c: ClaimEvent) => void }) {
  return (
    <article aria-label="Answer" aria-busy={turn.streaming}>
      {turn.claims.length > 0 && (
        <ul>
          {turn.claims.map((claim, i) => (
            <li key={i}>
              {claim.text} <Citation claim={claim} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      )}
      {turn.refused && <p>The documents do not answer this.</p>}
      {turn.streaming && turn.claims.length === 0 && <p>Thinking…</p>}
      {turn.error && <p role="alert">{turn.error}</p>}
    </article>
  )
}

export default function Chat({ collections }: { collections: string[] }) {
  const [chosen, setChosen] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [opened, setOpened] = useState<ClaimEvent | null>(null)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  const collection =
    chosen !== null && collections.includes(chosen)
      ? chosen
      : collections.includes(DEFAULT_COLLECTION)
        ? DEFAULT_COLLECTION
        : (collections[0] ?? '')
  const streaming = turns.some((t) => t.streaming)

  async function onAsk(e: FormEvent) {
    e.preventDefault()
    const q = question.trim()
    if (!q || !collection || streaming) return
    const index = turns.length
    const update = (f: (t: Turn) => Turn) =>
      setTurns((all) => all.map((t, i) => (i === index ? f(t) : t)))
    setTurns((all) => [
      ...all,
      { question: q, claims: [], refused: false, error: null, done: null, streaming: true },
    ])
    setQuestion('')

    const abort = new AbortController()
    controller.current = abort
    try {
      for await (const event of streamChat(collection, q, abort.signal)) {
        update((t) => apply(t, event))
        if (event.type === 'done' || event.type === 'error') return
      }
      // A stream that ends without `done` or `error` was cut off.
      update((t) => ({ ...t, error: 'The answer stopped before it finished.', streaming: false }))
    } catch (err) {
      if (abort.signal.aborted) return
      const message = err instanceof Error ? err.message : 'the answer failed'
      update((t) => ({ ...t, error: message, streaming: false }))
    }
  }

  return (
    <section aria-labelledby="chat-heading">
      <h2 id="chat-heading">Chat</h2>
      {turns.map((turn, i) => (
        <div key={i}>
          <article aria-label="Question">{turn.question}</article>
          <Answer turn={turn} onOpen={setOpened} />
        </div>
      ))}
      {opened && (
        <Suspense fallback={<p>Opening the PDF viewer…</p>}>
          <PdfViewer
            key={`${opened.chunk_id}:${opened.page}:${opened.quote}`}
            citation={opened}
            onClose={() => setOpened(null)}
          />
        </Suspense>
      )}
      <form onSubmit={onAsk}>
        <label>
          Ask in collection{' '}
          <select value={collection} onChange={(e) => setChosen(e.target.value)}>
            {collections.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>{' '}
        <label>
          Ask a question{' '}
          <input value={question} onChange={(e) => setQuestion(e.target.value)} />
        </label>{' '}
        <button type="submit" disabled={!question.trim() || !collection || streaming}>
          Ask
        </button>
        {collections.length === 0 && <p>Upload a document to ask about it.</p>}
      </form>
    </section>
  )
}
