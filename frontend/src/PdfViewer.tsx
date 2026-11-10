import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { GlobalWorkerOptions, TextLayer, getDocument } from 'pdfjs-dist'
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { documentPdfUrl, type ClaimEvent } from './api'
import { findQuote, type QuoteRange } from './quote'
import './PdfViewer.css'

GlobalWorkerOptions.workerSrc = workerSrc

export type Citation = Pick<ClaimEvent, 'document' | 'page' | 'quote' | 'boxes'>

// Widest the page is drawn, in CSS pixels; narrower when the viewer is.
const MAX_WIDTH = 800

interface Rect {
  left: number
  top: number
  width: number
  height: number
}

type View =
  | { state: 'loading' }
  | { state: 'error'; message: string }
  // `boxes` drawn from the API, else `located` says whether the text search found the quote.
  | { state: 'ready'; pages: number; boxes: Rect[] | null; located: boolean }

// What Tab can reach inside the viewer.
const FOCUSABLE =
  'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'

/** Wrap each range of the text layer's spans in a <mark>. */
function highlight(divs: HTMLElement[], ranges: QuoteRange[]): HTMLElement[] {
  return ranges.map(({ item, start, end }) => {
    const div = divs[item]
    const text = div.textContent ?? ''
    const mark = document.createElement('mark')
    mark.textContent = text.slice(start, end)
    div.replaceChildren(text.slice(0, start), mark, text.slice(end))
    return mark
  })
}

/** One cited page of a stored PDF, with the quote highlighted or a notice that it was not found. */
export default function PdfViewer({
  citation,
  onClose,
}: {
  citation: Citation
  onClose: () => void
}) {
  const { document: doc, page, quote, boxes } = citation
  const [view, setView] = useState<View>({ state: 'loading' })
  // Bumped by Retry to open the PDF again.
  const [attempt, setAttempt] = useState(0)
  const [size, setSize] = useState<{
    width: number
    height: number
    scale: number
  } | null>(null)
  const dialog = useRef<HTMLElement>(null)
  const frame = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const textLayer = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    let layer: TextLayer | null = null
    const task = getDocument({ url: documentPdfUrl(doc.id) })

    async function open() {
      const pdf = await task.promise
      if (page < 1 || page > pdf.numPages) throw new Error(`it has no page ${page}`)
      const pdfPage = await pdf.getPage(page)
      if (cancelled) return

      const available = frame.current?.clientWidth || MAX_WIDTH
      const scale = Math.min(available, MAX_WIDTH) / pdfPage.getViewport({ scale: 1 }).width
      const viewport = pdfPage.getViewport({ scale })
      const ratio = window.devicePixelRatio || 1
      const c = canvas.current!
      c.width = Math.floor(viewport.width * ratio)
      c.height = Math.floor(viewport.height * ratio)
      setSize({ width: viewport.width, height: viewport.height, scale })
      await pdfPage.render({
        canvas: c,
        viewport,
        transform: [ratio, 0, 0, ratio, 0, 0],
      }).promise
      if (cancelled) return

      const container = textLayer.current!
      container.replaceChildren()
      layer = new TextLayer({
        textContentSource: pdfPage.streamTextContent(),
        container,
        viewport,
      })
      await layer.render()
      if (cancelled) return

      // Bounding boxes are used only when the API returns them; otherwise the
      // quote is searched for in the text layer.
      if (boxes && boxes.length > 0) {
        const rects = boxes.map(({ x0, y0, x1, y1 }) => {
          const [ax, ay, bx, by] = viewport.convertToViewportRectangle([x0, y0, x1, y1])
          return {
            left: Math.min(ax, bx),
            top: Math.min(ay, by),
            width: Math.abs(bx - ax),
            height: Math.abs(by - ay),
          }
        })
        setView({
          state: 'ready',
          pages: pdf.numPages,
          boxes: rects,
          located: true,
        })
        return
      }
      const ranges = findQuote(layer.textContentItemsStr, quote)
      const marks = ranges ? highlight(layer.textDivs, ranges) : []
      setView({
        state: 'ready',
        pages: pdf.numPages,
        boxes: null,
        located: marks.length > 0,
      })
      marks[0]?.scrollIntoView?.({ block: 'center' })
    }

    open().catch((err: unknown) => {
      if (cancelled) return
      const reason = err instanceof Error ? err.message : String(err)
      setView({
        state: 'error',
        message: `Could not open ${doc.filename}: ${reason}`,
      })
    })
    return () => {
      cancelled = true
      layer?.cancel()
      void task.destroy()
    }
  }, [doc.id, doc.filename, page, quote, boxes, attempt])

  // Focus moves into the viewer when it opens; the opener puts it back on close.
  useEffect(() => dialog.current?.focus(), [])

  // Escape closes; Tab and Shift+Tab go round the viewer's controls, never out of it.
  function onKeyDown(e: KeyboardEvent<HTMLElement>) {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
      return
    }
    if (e.key !== 'Tab') return
    const controls = [...dialog.current!.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (el) => !el.closest('[hidden]'),
    )
    const first = controls[0]
    const last = controls[controls.length - 1]
    const at = document.activeElement
    if (e.shiftKey && (at === first || at === dialog.current)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && at === last) {
      e.preventDefault()
      first.focus()
    }
  }

  function onRetry() {
    setView({ state: 'loading' })
    setAttempt((n) => n + 1)
  }

  const pageStyle = (
    size
      ? {
          width: size.width,
          height: size.height,
          '--total-scale-factor': size.scale,
        }
      : undefined
  ) as CSSProperties | undefined

  return (
    <section
      ref={dialog}
      role="dialog"
      aria-modal="true"
      aria-label={`${doc.filename}, page ${page}`}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      aria-busy={view.state === 'loading'}
      className="pdf-viewer"
    >
      <header>
        <h3>
          {doc.filename}
          {view.state === 'ready' && (
            <>
              {' '}
              <span>
                Page {page} of {view.pages}
              </span>
            </>
          )}
        </h3>
        <button type="button" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </header>
      {view.state === 'loading' && (
        <p className="loading">
          <span className="spinner" aria-hidden="true" /> Opening page {page}…
        </p>
      )}
      {view.state === 'error' && (
        <div className="pdf-viewer-error">
          <p role="alert">{view.message}</p>
          <button type="button" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}
      <div className="pdf-viewer-body">
        <div ref={frame} className="pdf-viewer-frame" hidden={view.state === 'error'}>
          <div className="pdf-page" style={pageStyle}>
            <canvas ref={canvas} />
            <div ref={textLayer} className="textLayer" />
            {view.state === 'ready' &&
              view.boxes?.map((r, i) => (
                <div
                  key={i}
                  className="quote-box"
                  style={{
                    left: r.left,
                    top: r.top,
                    width: r.width,
                    height: r.height,
                  }}
                />
              ))}
          </div>
        </div>
        {view.state === 'ready' && !view.located && (
          <aside className="pdf-viewer-notice">
            <p role="status">Quote not located on this page.</p>
            <blockquote>{quote}</blockquote>
          </aside>
        )}
      </div>
    </section>
  )
}
