import { Suspense, lazy, useEffect, useRef, useState, type FormEvent } from 'react'
import AnswerMeta from './AnswerMeta'
import {
  streamChat,
  type ChatEvent,
  type ChatMode,
  type ClaimEvent,
  type DoneEvent,
  type Passage,
  type StepEvent,
} from './api'

// pdf.js is large; load the viewer the first time a citation is opened.
const PdfViewer = lazy(() => import('./PdfViewer'))

const DEFAULT_COLLECTION = 'default'

const DEEP_NOTE =
  'Deep mode uses the reasoning model: it plans sub-questions and searches for each, so it is slower and can cost more.'

interface Turn {
  question: string
  /** Where and how it was asked, so a retry asks it the same way. */
  collection: string
  mode: ChatMode
  /** Deep mode's sub-questions, in the order they arrived. */
  steps: StepEvent[]
  claims: ClaimEvent[]
  /** The closest passages of a refusal; `null` when the answer was not refused. */
  refusal: Passage[] | null
  error: string | null
  done: DoneEvent | null
  streaming: boolean
  /** The person stopped the answer before it finished. */
  stopped: boolean
}

const CUT_OFF = 'The answer stopped before it finished.'

function failed(message: string | undefined): string {
  return message?.trim() ? `The answer failed: ${message}` : 'The answer failed.'
}

function apply(turn: Turn, event: ChatEvent): Turn {
  switch (event.type) {
    case 'step':
      return { ...turn, steps: [...turn.steps, event] }
    case 'claim':
      return { ...turn, claims: [...turn.claims, event] }
    case 'refusal':
      return { ...turn, refusal: event.passages ?? [] }
    case 'error':
      return { ...turn, error: failed(event.message), streaming: false }
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

function Refusal({ passages }: { passages: Passage[] }) {
  return (
    <div className="refusal">
      <h3>Not in your documents</h3>
      <p>The documents do not answer this.</p>
      {passages.length > 0 ? (
        <>
          <p>The closest passages:</p>
          <ul aria-label="Closest passages">
            {passages.map((passage, i) => (
              <li key={i}>
                <span>
                  {passage.document.filename}, p. {passage.page}
                </span>
                <blockquote>{passage.text}</blockquote>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>No passage came close.</p>
      )}
    </div>
  )
}

function Steps({ steps }: { steps: StepEvent[] }) {
  return (
    <ol aria-label="Steps" className="steps">
      {steps.map((step, i) => (
        <li key={i}>
          {step.question}{' '}
          <span className="step-pages">
            {step.pages?.length
              ? step.pages.map((p) => `${p.document.filename}, p. ${p.page}`).join('; ')
              : 'no pages found'}
          </span>
        </li>
      ))}
    </ol>
  )
}

function Answer({
  turn,
  onOpen,
  onRetry,
}: {
  turn: Turn
  onOpen: (c: ClaimEvent) => void
  /** Ask the same question again; `undefined` while another answer streams. */
  onRetry?: () => void
}) {
  // A finished answer with no claim, no refusal and no error would show nothing.
  const empty =
    turn.done !== null && turn.claims.length === 0 && turn.refusal === null && !turn.error
  return (
    <article aria-label="Answer" aria-busy={turn.streaming}>
      {turn.steps.length > 0 && <Steps steps={turn.steps} />}
      {turn.claims.length > 0 && (
        <ul aria-label="Claims">
          {turn.claims.map((claim, i) => (
            <li key={i}>
              {claim.text} <Citation claim={claim} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      )}
      {turn.refusal && <Refusal passages={turn.refusal} />}
      {turn.streaming && turn.claims.length === 0 && turn.refusal === null && <p>Thinking…</p>}
      {turn.error && (
        <>
          <p role="alert">{turn.error}</p>
          <button type="button" onClick={onRetry} disabled={!onRetry}>
            Retry
          </button>
        </>
      )}
      {empty && <p role="alert">The answer came back empty.</p>}
      {turn.stopped && <p role="status">You stopped this answer before it finished.</p>}
      {turn.done && <AnswerMeta done={turn.done} />}
    </article>
  )
}

function stillReading(files: string[]): string {
  const one = files.length === 1
  return `Still reading ${files.join(', ')}. Answers leave ${one ? 'it' : 'them'} out until ${
    one ? 'it is' : 'they are'
  } ready.`
}

export default function Chat({
  collections,
  loading = false,
  empty = false,
  ingesting = {},
}: {
  collections: string[]
  /** The collections are still loading for the first time. */
  loading?: boolean
  /** The collections loaded, and none holds a document yet. */
  empty?: boolean
  /** Filenames still being read, by collection name. */
  ingesting?: Record<string, string[]>
}) {
  const [chosen, setChosen] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<ChatMode>('fast')
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

  function onAsk(e: FormEvent) {
    e.preventDefault()
    const q = question.trim()
    if (!q || !collection || streaming) return
    setQuestion('')
    void run({ question: q, collection, mode }, turns.length)
  }

  /** Stream the answer into turn `index`: a new turn, or a failed one asked again. */
  async function run(
    { question: q, collection, mode }: Pick<Turn, 'question' | 'collection' | 'mode'>,
    index: number,
  ) {
    const update = (f: (t: Turn) => Turn) =>
      setTurns((all) => all.map((t, i) => (i === index ? f(t) : t)))
    const fresh: Turn = {
      question: q,
      collection,
      mode,
      steps: [],
      claims: [],
      refusal: null,
      error: null,
      done: null,
      streaming: true,
      stopped: false,
    }
    setTurns((all) =>
      index < all.length ? all.map((t, i) => (i === index ? fresh : t)) : [...all, fresh],
    )

    const abort = new AbortController()
    controller.current = abort
    try {
      for await (const event of streamChat(collection, q, abort.signal, mode)) {
        if (abort.signal.aborted) break
        update((t) => apply(t, event))
        if (event.type === 'done' || event.type === 'error') return
      }
      if (!abort.signal.aborted) {
        // A stream that ends without `done` or `error` was cut off.
        update((t) => ({ ...t, error: CUT_OFF, streaming: false }))
        return
      }
    } catch (err) {
      if (!abort.signal.aborted) {
        update((t) => ({
          ...t,
          error: failed(err instanceof Error ? err.message : undefined),
          streaming: false,
        }))
        return
      }
    }
    update((t) => ({ ...t, stopped: true, streaming: false }))
  }

  return (
    <section aria-labelledby="chat-heading" aria-busy={loading} className="panel chat">
      <h2 id="chat-heading">Chat</h2>
      {loading && (
        <p className="loading">
          <span className="spinner" aria-hidden="true" /> Loading your documents…
        </p>
      )}
      {!loading && turns.length === 0 && empty && (
        <div className="empty-state">
          <h3>Nothing to ask about yet</h3>
          <p>
            Upload a PDF in the Documents panel, then ask a question about it here. Every answer
            cites the page it came from.
          </p>
        </div>
      )}
      {turns.map((turn, i) => (
        <div key={i} className="chat-turn">
          <article aria-label="Question" className="question">{turn.question}</article>
          <Answer
            turn={turn}
            onOpen={setOpened}
            onRetry={streaming ? undefined : () => void run(turn, i)}
          />
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
      <form onSubmit={onAsk} className="chat-form">
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
        <label className="question-field">
          Ask a question{' '}
          <input value={question} onChange={(e) => setQuestion(e.target.value)} />
        </label>{' '}
        <label>
          <input
            type="checkbox"
            role="switch"
            checked={mode === 'deep'}
            onChange={(e) => setMode(e.target.checked ? 'deep' : 'fast')}
          />{' '}
          Deep mode
        </label>{' '}
        <button type="submit" disabled={!question.trim() || !collection || streaming}>
          Ask
        </button>
        {streaming && (
          <>
            {' '}
            <button type="button" onClick={() => controller.current?.abort()}>
              Stop
            </button>
          </>
        )}
        {mode === 'deep' && <p className="deep-note">{DEEP_NOTE}</p>}
        {ingesting[collection]?.length ? (
          <p className="ingesting-note">{stillReading(ingesting[collection])}</p>
        ) : null}
      </form>
    </section>
  )
}
