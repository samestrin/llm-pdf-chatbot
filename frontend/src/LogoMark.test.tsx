import { render, screen } from '@testing-library/react'
import LogoMark from './LogoMark'

describe('LogoMark', () => {
  it('is decoration only, so the heading still reads as the app name', () => {
    render(
      <h1>
        <LogoMark />
        LLM PDF Chatbot
      </h1>,
    )
    expect(screen.getByRole('heading', { name: 'LLM PDF Chatbot' })).toBeInTheDocument()
    const svg = document.querySelector('svg.logo-mark')
    expect(svg).toHaveAttribute('aria-hidden', 'true')
    // The colors come from the theme tokens, not fixed fills.
    expect(svg?.querySelector('.logo-mark-page')).not.toHaveAttribute('fill')
    expect(svg?.querySelector('.logo-mark-fold')).not.toHaveAttribute('fill')
  })
})
