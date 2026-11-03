export type Theme = 'light' | 'dark'

export const THEME_KEY = 'theme'

export function systemTheme(): Theme {
  return typeof matchMedia === 'function' && matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light'
}

/** The saved choice, or null when the person has not chosen (follow the system). */
export function savedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(THEME_KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null
  }
}

export function saveTheme(theme: Theme) {
  try {
    localStorage.setItem(THEME_KEY, theme)
  } catch {
    // Storage can be blocked; the choice then lasts until the page closes.
  }
}

/** Set (or, with null, clear) the explicit theme on the page; CSS reads `data-theme`. */
export function applyTheme(theme: Theme | null) {
  if (theme) document.documentElement.dataset.theme = theme
  else delete document.documentElement.dataset.theme
}
