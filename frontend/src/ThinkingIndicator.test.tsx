import { render, screen } from '@testing-library/react'
import ThinkingIndicator from './ThinkingIndicator'

describe('ThinkingIndicator', () => {
  it('says "Thinking…" in text and keeps the dots out of the accessible name', () => {
    render(<ThinkingIndicator />)
    // The dots come after the word, and a screen reader hears "Thinking…".
    expect(screen.getByText('Thinking')).toBeInTheDocument()
    expect(document.querySelector('.thinking')?.textContent).toBe('Thinking…')
    const dots = document.querySelector('.thinking-dots')
    expect(dots).toHaveAttribute('aria-hidden', 'true')
    expect(dots?.querySelectorAll('.thinking-dot')).toHaveLength(3)
    expect(document.querySelector('.thinking')?.firstElementChild).toHaveClass('thinking-label')
  })
})
