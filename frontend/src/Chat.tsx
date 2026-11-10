import { Suspense, lazy, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import AnswerMeta from './AnswerMeta'
import {
  streamChat,
  type ChatEvent,
  type ChatMode,
  type CitedDocument,
  type ClaimEvent,
  type DoneEvent,
  type Passage,
  type StepEvent,
} from './api'
import ThinkingIndicator from './ThinkingIndicator'

// pdf.js is large; load the viewer the first time a citation is opened.
const PdfViewer = lazy(() => import('./PdfViewer'))

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

type OnOpen = (c: ClaimEvent, chip: HTMLButtonElement) => void

/**
 * The documents an answer cites, numbered in the order they first appear (steps, claims,
 * then refusal passages). With one document a citation is just `p. N` and the name is
 * shown once; with more, a citation is `n · p. N` and the names are listed as sources.
 */
class Sources {
  readonly documents: CitedDocument[] = []
  private readonly index = new Map<string, number>()

  constructor(items: { document: CitedDocument }[]) {
    for (const { document } of items) {
      if (this.index.has(document.id)) continue
      this.index.set(document.id, this.documents.length + 1)
      this.documents.push(document)
    }
  }

  get numbered(): boolean {
    return this.documents.length > 1
  }

  /** The short form of a citation: `p. 2`, or `1 · p. 2` when there are several sources. */
  short(document: CitedDocument, page: number): string {
    return this.numbered ? `${this.index.get(document.id)} · p. ${page}` : `p. ${page}`
  }
}

/** A file name on one line, cut with an ellipsis, the whole name in its tooltip. */
function FileName({ name }: { name: string }) {
  return (
    <span className="filename" title={name}>
      {name}
    </span>
  )
}

/** Sorted page numbers as ranges of consecutive pages: [1, 2, 3, 5] is `pp. 1–3, 5`. */
export function pageRanges(pages: number[]): string {
  const sorted = [...new Set(pages)].sort((a, b) => a - b)
  const ranges: string[] = []
  for (let i = 0; i < sorted.length; ) {
    let j = i
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++
    ranges.push(j === i ? `${sorted[i]}` : `${sorted[i]}–${sorted[j]}`)
    i = j + 1
  }
  return `${sorted.length === 1 ? 'p.' : 'pp.'} ${ranges.join(', ')}`
}

/**
 * The line above the claims, built from the claims alone, so it can name nothing no claim
 * cites: `Found in lease.pdf, pp. 1–3`, or `Found in 2 documents, 5 points`.
 */
function LeadLine({ claims }: { claims: ClaimEvent[] }) {
  const documents = new Sources(claims).documents
  if (documents.length === 1) {
    return (
      <p className="answer-lead">
        <span>Found in </span>
        <FileName name={documents[0].filename} />
        <span>, {pageRanges(claims.map((c) => c.page))}</span>
      </p>
    )
  }
  return (
    <p className="answer-lead">
      Found in {documents.length} documents, {claims.length} points
    </p>
  )
}

function SourceList({ sources }: { sources: Sources }) {
  if (!sources.numbered) {
    return (
      <p className="answer-sources">
        <span>Source: </span>
        <FileName name={sources.documents[0].filename} />
      </p>
    )
  }
  return (
    <ol aria-label="Sources" className="answer-sources">
      {sources.documents.map((document, i) => (
        <li key={document.id}>
          <span className="source-number">{i + 1}</span> <FileName name={document.filename} />
        </li>
      ))}
    </ol>
  )
}

function Citation({
  claim,
  sources,
  onOpen,
}: {
  claim: ClaimEvent
  sources: Sources
  onOpen: OnOpen
}) {
  return (
    <span role="note" aria-label="Citation" className="citation-chip" title={claim.quote}>
      <button
        type="button"
        aria-label={`${claim.document.filename}, p. ${claim.page}`}
        onClick={(e) => onOpen(claim, e.currentTarget)}
      >
        {sources.short(claim.document, claim.page)}
      </button>
    </span>
  )
}

function Refusal({ passages, sources }: { passages: Passage[]; sources: Sources }) {
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
                <span>{sources.short(passage.document, passage.page)}</span>
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

function Steps({ steps, sources }: { steps: StepEvent[]; sources: Sources }) {
  return (
    <ol aria-label="Steps" className="steps">
      {steps.map((step, i) => (
        <li key={i}>
          {step.question}{' '}
          <span className="step-pages">
            {step.pages?.length
              ? step.pages.map((p) => sources.short(p.document, p.page)).join('; ')
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
  onOpen: OnOpen
  /** Ask the same question again; `undefined` while another answer streams. */
  onRetry?: () => void
}) {
  // A finished answer with no claim, no refusal and no error would show nothing.
  const empty =
    turn.done !== null && turn.claims.length === 0 && turn.refusal === null && !turn.error
  const sources = new Sources([
    ...turn.steps.flatMap((s) => s.pages ?? []),
    ...turn.claims,
    ...(turn.refusal ?? []),
  ])
  // The lead line goes with claims that stand: never on a refusal or an error.
  const lead = turn.claims.length > 0 && turn.refusal === null && !turn.error
  // With one source the lead line already names it once.
  const listSources = sources.documents.length > 0 && !(lead && !sources.numbered)
  return (
    // Polite: a screen reader reads the claims as they stream in, without cutting in.
    <article aria-label="Answer" aria-busy={turn.streaming} aria-live="polite">
      {turn.steps.length > 0 && <Steps steps={turn.steps} sources={sources} />}
      {lead && <LeadLine claims={turn.claims} />}
      {turn.claims.length > 0 && (
        <ul aria-label="Claims">
          {turn.claims.map((claim, i) => (
            <li key={i}>
              {claim.text} <Citation claim={claim} sources={sources} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      )}
      {turn.refusal && <Refusal passages={turn.refusal} sources={sources} />}
      {turn.streaming && turn.claims.length === 0 && turn.refusal === null && (
        <ThinkingIndicator />
      )}
      {turn.error && (
        <>
          <p role="alert">{turn.error}</p>
          <button type="button" className="secondary" onClick={onRetry} disabled={!onRetry}>
            Retry
          </button>
        </>
      )}
      {empty && <p role="alert">The answer came back empty.</p>}
      {turn.stopped && <p role="status">You stopped this answer before it finished.</p>}
      {listSources && <SourceList sources={sources} />}
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
  collection,
  loading = false,
  empty = false,
  ingesting = {},
}: {
  /** The active collection, chosen in the documents panel; '' while none is named. */
  collection: string
  /** The collections are still loading for the first time. */
  loading?: boolean
  /** The collections loaded, and none holds a document yet. */
  empty?: boolean
  /** Filenames still being read, by collection name. */
  ingesting?: Record<string, string[]>
}) {
  const [question, setQuestion] = useState('')
  const [mode, setMode] = useState<ChatMode>('fast')
  const [turns, setTurns] = useState<Turn[]>([])
  const [opened, setOpened] = useState<ClaimEvent | null>(null)
  // The chip that opened the viewer, so closing it puts focus back there.
  const chip = useRef<HTMLButtonElement | null>(null)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  const streaming = turns.some((t) => t.streaming)

  function onAsk(e: FormEvent) {
    e.preventDefault()
    const q = question.trim()
    if (!q || !collection || streaming) return
    setQuestion('')
    void run({ question: q, collection, mode }, turns.length)
  }

  // Enter asks; Shift+Enter, or Enter while an input method composes, is left to the browser.
  function onQuestionKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return
    e.preventDefault()
    e.currentTarget.form?.requestSubmit()
  }

  function onOpen(claim: ClaimEvent, from: HTMLButtonElement) {
    chip.current = from
    setOpened(claim)
  }

  function onClose() {
    setOpened(null)
    chip.current?.focus()
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
            onOpen={onOpen}
            onRetry={streaming ? undefined : () => void run(turn, i)}
          />
        </div>
      ))}
      {opened && (
        <Suspense fallback={<p>Opening the PDF viewer…</p>}>
          <PdfViewer
            key={`${opened.chunk_id}:${opened.page}:${opened.quote}`}
            citation={opened}
            onClose={onClose}
          />
        </Suspense>
      )}
      <form onSubmit={onAsk} className="chat-form">
        {collection && (
          <p className="asking-in">
            <span>Asking in </span>
            <FileName name={collection} />
          </p>
        )}
        <label className="question-field">
          <span className="visually-hidden">Ask a question</span>
          <textarea
            rows={2}
            placeholder="Ask a question about the PDFs in this collection"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onQuestionKey}
          />
        </label>
        <div className="chat-actions">
          <div className="chat-options">
            <label>
              <input
                type="checkbox"
                role="switch"
                checked={mode === 'deep'}
                onChange={(e) => setMode(e.target.checked ? 'deep' : 'fast')}
              />{' '}
              Deep mode
            </label>
            {mode === 'deep' && <p className="deep-note">{DEEP_NOTE}</p>}
          </div>
          <div className="chat-buttons">
            {streaming && (
              <button type="button" className="secondary" onClick={() => controller.current?.abort()}>
                Stop
              </button>
            )}
            <button type="submit" disabled={!question.trim() || !collection || streaming}>
              Ask
            </button>
          </div>
        </div>
        {ingesting[collection]?.length ? (
          <p className="ingesting-note">{stillReading(ingesting[collection])}</p>
        ) : null}
      </form>
    </section>
  )
}
