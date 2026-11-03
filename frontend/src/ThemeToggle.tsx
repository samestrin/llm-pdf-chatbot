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
    <button type="button" className="theme-toggle" aria-pressed={theme === 'dark'} onClick={toggle}>
      Dark theme
    </button>
  )
}
