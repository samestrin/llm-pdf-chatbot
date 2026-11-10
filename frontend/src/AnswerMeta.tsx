import type { DoneEvent } from './api'

/** Whether the backend priced the answer; `null` means it could not, which is not 0. */
export function isKnownCost(cost: number | null): cost is number {
  return cost !== null && Number.isFinite(cost) && cost >= 0
}

/** A cost in US dollars; `null` means the backend could not price the answer, which is not 0. */
export function formatCost(cost: number | null): string {
  if (!isKnownCost(cost)) return 'cost unknown'
  if (cost === 0) return '$0'
  if (cost < 0.0001) return 'under $0.0001'
  // Answers cost fractions of a cent: keep four significant digits, no float noise.
  return `$${Number(cost.toPrecision(4))}`
}

/** The mode and model of a finished answer, and its cost when the cost is known. */
export default function AnswerMeta({ done }: { done: DoneEvent }) {
  return (
    <p aria-label="Answer details" className="answer-meta">
      {done.mode} · {done.model}
      {isKnownCost(done.cost) && ` · ${formatCost(done.cost)}`}
    </p>
  )
}
