import { fireEvent, render, screen } from '@testing-library/react'
import ThemeToggle from './ThemeToggle'

function systemPrefers(dark: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: dark && query.includes('dark') }))
}

describe('ThemeToggle', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
    delete document.documentElement.dataset.theme
  })

  it('starts from the system scheme', () => {
    systemPrefers(true)
    render(<ThemeToggle />)
    // The button names what it does next: in dark it offers light.
    expect(screen.getByRole('button', { name: 'Light theme' })).not.toHaveAttribute('aria-pressed')
  })

  it('a saved choice beats the system scheme', () => {
    systemPrefers(true)
    localStorage.setItem('theme', 'light')
    render(<ThemeToggle />)
    expect(screen.getByRole('button', { name: 'Dark theme' })).not.toHaveAttribute('aria-pressed')
  })

  it('saves the choice and sets it on the page', () => {
    systemPrefers(false)
    render(<ThemeToggle />)
    fireEvent.click(screen.getByRole('button', { name: 'Dark theme' }))
    expect(localStorage.getItem('theme')).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    // The label flips so the way back is clear.
    fireEvent.click(screen.getByRole('button', { name: 'Light theme' }))
    expect(localStorage.getItem('theme')).toBe('light')
    expect(screen.getByRole('button', { name: 'Dark theme' })).toBeInTheDocument()
  })
})
