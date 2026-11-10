import { useState } from 'react'
import { applyTheme, saveTheme, savedTheme, systemTheme, type Theme } from './theme'

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => savedTheme() ?? systemTheme())

  function toggle() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    saveTheme(next)
    applyTheme(next)
  }

  return (
    // The label names what a click does, so the way back is always visible.
    <button type="button" className="theme-toggle" onClick={toggle}>
      {theme === 'dark' ? 'Light theme' : 'Dark theme'}
    </button>
  )
}
