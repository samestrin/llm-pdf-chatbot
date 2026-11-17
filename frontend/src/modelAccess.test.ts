import {
  clearCredentials,
  loadCredentials,
  modelHeaders,
  saveCredentials,
  STORAGE_KEY,
} from './modelAccess'

describe('modelAccess storage', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
  })

  it('keeps the key in session storage by default, never in local storage', () => {
    saveCredentials({ preset: 'openai', secret: 'sk-visitor-key' }, false)
    expect(JSON.parse(sessionStorage.getItem(STORAGE_KEY)!)).toEqual({
      preset: 'openai',
      secret: 'sk-visitor-key',
      remember: false,
    })
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(loadCredentials()).toEqual({
      preset: 'openai',
      secret: 'sk-visitor-key',
      remember: false,
    })
  })

  it('keeps the key in local storage only when the visitor asks to remember it', () => {
    saveCredentials({ preset: 'openai', secret: 'sk-visitor-key' }, true)
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(loadCredentials()).toEqual({
      preset: 'openai',
      secret: 'sk-visitor-key',
      remember: true,
    })
  })

  it('moves the key between the stores when remember changes', () => {
    saveCredentials({ preset: 'openai', secret: 'k' }, true)
    saveCredentials({ preset: 'openai', secret: 'k' }, false)
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull()
    saveCredentials({ preset: 'demo', secret: 'code' }, true)
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(loadCredentials()).toEqual({ preset: 'demo', secret: 'code', remember: true })
  })

  it('clear wipes both stores', () => {
    saveCredentials({ preset: 'openai', secret: 'k' }, true)
    saveCredentials({ preset: 'openai', secret: 'k2' }, false)
    clearCredentials()
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(loadCredentials()).toBeNull()
  })

  it('loads nothing when nothing is saved', () => {
    expect(loadCredentials()).toBeNull()
  })

  it.each(['not json', '{"preset":"openai"}', '{"preset":"","secret":"k"}', '[]', 'null'])(
    'ignores a malformed stored value: %s',
    (raw) => {
      sessionStorage.setItem(STORAGE_KEY, raw)
      expect(loadCredentials()).toBeNull()
    },
  )

  it('falls back to local storage when session storage holds nothing usable', () => {
    sessionStorage.setItem(STORAGE_KEY, 'broken')
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ preset: 'demo', secret: 'code', remember: true }),
    )
    expect(loadCredentials()).toEqual({ preset: 'demo', secret: 'code', remember: true })
  })
})

describe('modelHeaders', () => {
  it('sends a provider key as X-Model-Key with its preset', () => {
    expect(modelHeaders({ preset: 'openai', secret: 'sk-k' })).toEqual({
      'X-Model-Preset': 'openai',
      'X-Model-Key': 'sk-k',
    })
  })

  it('sends a demo code as X-Demo-Code, never as X-Model-Key', () => {
    expect(modelHeaders({ preset: 'demo', secret: 'demo-code' })).toEqual({
      'X-Model-Preset': 'demo',
      'X-Demo-Code': 'demo-code',
    })
  })
})