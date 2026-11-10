import { render, screen } from '@testing-library/react'
import AnswerMeta, { formatCost } from './AnswerMeta'

describe('formatCost', () => {
  it('never shows an unknown cost as 0', () => {
    expect(formatCost(null)).toBe('cost unknown')
    expect(formatCost(Number.NaN)).toBe('cost unknown')
    expect(formatCost(-1)).toBe('cost unknown')
  })

  it('shows a real zero, tiny costs and fractions of a cent', () => {
    expect(formatCost(0)).toBe('$0')
    expect(formatCost(0.00000012)).toBe('under $0.0001')
    expect(formatCost(0.0021)).toBe('$0.0021')
    expect(formatCost(0.1 + 0.2)).toBe('$0.3')
    expect(formatCost(1.23456)).toBe('$1.235')
  })
})

describe('AnswerMeta', () => {
  it('shows mode and model as text, and no cost when it is unknown', () => {
    render(<AnswerMeta done={{ type: 'done', mode: 'fast', model: '<b>m</b>', cost: null }} />)
    const meta = screen.getByLabelText('Answer details')
    expect(meta.textContent).toBe('fast · <b>m</b>')
    expect(meta).not.toHaveTextContent('cost unknown')
    expect(meta.querySelector('b')).toBeNull()
  })

  it('shows a known cost, a real zero included', () => {
    const { rerender } = render(
      <AnswerMeta done={{ type: 'done', mode: 'fast', model: 'm', cost: 0.0021 }} />,
    )
    expect(screen.getByLabelText('Answer details').textContent).toBe('fast · m · $0.0021')
    rerender(<AnswerMeta done={{ type: 'done', mode: 'fast', model: 'm', cost: 0 }} />)
    expect(screen.getByLabelText('Answer details').textContent).toBe('fast · m · $0')
  })
})
