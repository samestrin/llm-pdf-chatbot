import {
  activeCredentials,
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

  it('keeps the keys in session storage by default, never in local storage', () => {
    saveCredentials({ preset: 'openai', keys: { openai: 'sk-visitor-key' } }, false)
    expect(JSON.parse(sessionStorage.getItem(STORAGE_KEY)!)).toEqual({
      preset: 'openai',
      keys: { openai: 'sk-visitor-key' },
      remember: false,
    })
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(loadCredentials()).toEqual({
      preset: 'openai',
      keys: { openai: 'sk-visitor-key' },
      remember: false,
    })
  })

  it('keeps the keys in local storage only when the visitor asks to remember them', () => {
    saveCredentials({ preset: 'openai', keys: { openai: 'sk-visitor-key' } }, true)
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(loadCredentials()).toEqual({
      preset: 'openai',
      keys: { openai: 'sk-visitor-key' },
      remember: true,
    })
  })

  it('moves every key between the stores when remember changes', () => {
    saveCredentials({ preset: 'openai', keys: { openai: 'k', demo: 'code' } }, true)
    saveCredentials({ preset: 'openai', keys: { openai: 'k', demo: 'code' } }, false)
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(loadCredentials()).toEqual({
      preset: 'openai',
      keys: { openai: 'k', demo: 'code' },
      remember: false,
    })
    saveCredentials({ preset: 'demo', keys: { openai: 'k', demo: 'code' } }, true)
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(loadCredentials()).toEqual({
      preset: 'demo',
      keys: { openai: 'k', demo: 'code' },
      remember: true,
    })
  })

  it('keeps a key per preset: switching the active preset keeps the other key', () => {
    saveCredentials({ preset: 'demo', keys: { demo: 'code' } }, true)
    const saved = loadCredentials()!
    saveCredentials({ preset: 'openai', keys: { ...saved.keys, openai: 'k' } }, true)
    expect(loadCredentials()).toEqual({
      preset: 'openai',
      keys: { demo: 'code', openai: 'k' },
      remember: true,
    })
  })

  it('clear wipes every key from both stores', () => {
    saveCredentials({ preset: 'openai', keys: { openai: 'k', demo: 'code' } }, true)
    saveCredentials({ preset: 'openai', keys: { openai: 'k2' } }, false)
    clearCredentials()
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
    expect(loadCredentials()).toBeNull()
  })

  it('loads nothing when nothing is saved', () => {
    expect(loadCredentials()).toBeNull()
  })

  it.each([
    'not json',
    '{"preset":"openai"}',
    '{"preset":"","secret":"k"}',
    '[]',
    'null',
    '{"preset":"openai","keys":{}}',
    '{"preset":"openai","keys":[]}',
    '{"preset":"openai","keys":"k"}',
    '{"preset":"openai","keys":{"openai":5}}',
    '{"preset":"openai","keys":{"openai":""}}',
  ])('ignores a malformed stored value: %s', (raw) => {
    sessionStorage.setItem(STORAGE_KEY, raw)
    expect(loadCredentials()).toBeNull()
  })

  it('drops unusable entries of the keys map but keeps the valid ones', () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        preset: 'openai',
        keys: { openai: 'k', bad: 5, empty: '', demo: 'code' },
        remember: false,
      }),
    )
    expect(loadCredentials()).toEqual({
      preset: 'openai',
      keys: { openai: 'k', demo: 'code' },
      remember: false,
    })
  })

  it('falls back to local storage when session storage holds nothing usable', () => {
    sessionStorage.setItem(STORAGE_KEY, 'broken')
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ preset: 'demo', keys: { demo: 'code' }, remember: true }),
    )
    expect(loadCredentials()).toEqual({ preset: 'demo', keys: { demo: 'code' }, remember: true })
  })

  it('migrates the old single-key shape from session storage under its old preset', () => {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ preset: 'demo', secret: 'demo-code', remember: false }),
    )
    expect(loadCredentials()).toEqual({
      preset: 'demo',
      keys: { demo: 'demo-code' },
      remember: false,
    })
  })

  it('migrates the old single-key shape from local storage, keeping remember', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ preset: 'openai', secret: 'sk-old', remember: true }),
    )
    expect(loadCredentials()).toEqual({
      preset: 'openai',
      keys: { openai: 'sk-old' },
      remember: true,
    })
  })
})

describe('activeCredentials', () => {
  it('picks the active preset’s own key, never another preset’s', () => {
    const stored = { preset: 'demo', keys: { demo: 'code', openai: 'sk-k' }, remember: true }
    expect(activeCredentials(stored)).toEqual({ preset: 'demo', secret: 'code' })
    expect(activeCredentials({ ...stored, preset: 'openai' })).toEqual({
      preset: 'openai',
      secret: 'sk-k',
    })
  })

  it('is null when nothing is stored or the active preset has no key', () => {
    expect(activeCredentials(null)).toBeNull()
    expect(activeCredentials({ preset: 'demo', keys: { openai: 'sk-k' }, remember: false })).toBeNull()
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
