// The visitor's model credentials in the browser: a preset id and one key or demo
// code per preset, so switching provider never forgets another provider's key.
// Session storage by default; local storage only when they tick "remember on
// this device". A secret goes to the backend only as a request header (modelHeaders)
// with its own preset, and is never put in a URL, a body or a log line.

export interface ModelCredentials {
  /** A preset id from `GET /presets`; `demo` means the secret is a demo code. */
  preset: string
  secret: string
}

/** The last chosen preset and each preset's own key or demo code. */
export interface PresetKeys {
  preset: string
  keys: Record<string, string>
}

export interface StoredCredentials extends PresetKeys {
  remember: boolean
}

export const STORAGE_KEY = 'pdfchat.model'

function parse(raw: string | null): StoredCredentials | null {
  if (raw === null) return null
  try {
    const value = JSON.parse(raw)
    if (value && typeof value.preset === 'string' && value.preset) {
      const remember = value.remember === true
      // The old single-key shape: keep its key under its old preset.
      if (typeof value.secret === 'string' && value.secret) {
        return { preset: value.preset, keys: { [value.preset]: value.secret }, remember }
      }
      if (value.keys && typeof value.keys === 'object' && !Array.isArray(value.keys)) {
        const keys: Record<string, string> = {}
        for (const [id, key] of Object.entries(value.keys)) {
          if (id && typeof key === 'string' && key) keys[id] = key
        }
        if (Object.keys(keys).length > 0) return { preset: value.preset, keys, remember }
      }
    }
  } catch {
    // A malformed stored value is no credentials.
  }
  return null
}

/** The saved credentials, session storage first; `null` when none are usable. */
export function loadCredentials(): StoredCredentials | null {
  try {
    return parse(sessionStorage.getItem(STORAGE_KEY)) ?? parse(localStorage.getItem(STORAGE_KEY))
  } catch {
    // Storage can be blocked (private browsing); the page then holds none.
    return null
  }
}

/** Save into one store only: local with `remember`, session without. */
export function saveCredentials(credentials: PresetKeys, remember: boolean): void {
  const value: StoredCredentials = { ...credentials, remember }
  try {
    const keep = remember ? localStorage : sessionStorage
    const drop = remember ? sessionStorage : localStorage
    keep.setItem(STORAGE_KEY, JSON.stringify(value))
    drop.removeItem(STORAGE_KEY)
  } catch {
    // Blocked storage: the credentials still live in the page's state for this visit.
  }
}

/** Wipe every stored key, the preset and remember from both stores. */
export function clearCredentials(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY)
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing was stored if storage is blocked.
  }
}

/** The active preset's own key for a model call; `null` when it has none. */
export function activeCredentials(stored: StoredCredentials | null): ModelCredentials | null {
  const secret = stored?.keys[stored.preset]
  return stored && secret ? { preset: stored.preset, secret } : null
}

/** The request headers for a model call: the secret is a header value, nothing else. */
export function modelHeaders(credentials: ModelCredentials): Record<string, string> {
  return credentials.preset === 'demo'
    ? { 'X-Model-Preset': 'demo', 'X-Demo-Code': credentials.secret }
    : { 'X-Model-Preset': credentials.preset, 'X-Model-Key': credentials.secret }
}
