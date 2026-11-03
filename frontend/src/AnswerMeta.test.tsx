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
  it('shows mode, model and cost as text', () => {
    render(<AnswerMeta done={{ type: 'done', mode: 'fast', model: '<b>m</b>', cost: null }} />)
    const meta = screen.getByLabelText('Answer details')
    expect(meta).toHaveTextContent('fast · <b>m</b> · cost unknown')
    expect(meta.querySelector('b')).toBeNull()
  })
})
