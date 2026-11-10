/** "Thinking" with three bouncing dots after it, shown while an answer has not started yet. */
export default function ThinkingIndicator() {
  return (
    <p className="thinking">
      <span className="thinking-label">Thinking</span>
      <span className="visually-hidden">…</span>
      <span className="thinking-dots" aria-hidden="true">
        <span className="thinking-dot" />
        <span className="thinking-dot" />
        <span className="thinking-dot" />
      </span>
    </p>
  )
}
