import { useId, useState, type Ref } from 'react'
import type { Preset } from './api'
import type { PresetKeys, StoredCredentials } from './modelAccess'

/**
 * The key panel of a public demo, open at the top of the page as the demo shell has
 * it: the public notice, then a preset dropdown fed by `GET /presets`, the visitor's
 * own key or a demo code per provider, a remember box and a clear button. Every
 * change is saved as it is made (session storage, or local storage with "remember",
 * by the page); switching provider shows that provider's key, never another's. Each
 * key is sent only as a request header with its own preset; Clear wipes every key
 * from both stores.
 */
export default function KeyPanel({
  presets,
  notice,
  embeddingModel = null,
  stored,
  onSave,
  onClear,
  fieldRef,
}: {
  presets: Preset[]
  /** The public notice, the panel's first line; no line when empty. */
  notice: string
  /** The model the server embeds every document and question with; null when none. */
  embeddingModel?: string | null
  stored: StoredCredentials | null
  /** Called on every change with the chosen preset and each preset's non-blank key. */
  onSave: (credentials: PresetKeys, remember: boolean) => void
  onClear: () => void
  /** The key field, so the page can send the visitor to it. */
  fieldRef?: Ref<HTMLInputElement>
}) {
  const id = useId()
  // The stored preset if the server still offers it, else the first one.
  const [presetId, setPresetId] = useState(
    presets.find((p) => p.id === stored?.preset)?.id ?? presets[0]?.id ?? '',
  )
  // Each preset's key as typed, so switching provider and back keeps it.
  const [keys, setKeys] = useState<Record<string, string>>(stored?.keys ?? {})
  const [remember, setRemember] = useState(stored?.remember ?? false)
  const preset = presets.find((p) => p.id === presetId)
  const secret = keys[presetId] ?? ''

  function save(next: { preset?: string; keys?: Record<string, string>; remember?: boolean }) {
    const kept: Record<string, string> = {}
    for (const [id, value] of Object.entries(next.keys ?? keys)) {
      const key = value.trim()
      if (key) kept[id] = key
    }
    onSave({ preset: next.preset ?? presetId, keys: kept }, next.remember ?? remember)
  }

  function onPresetChange(next: string) {
    setPresetId(next)
    save({ preset: next })
  }

  function onSecretChange(value: string) {
    const next = { ...keys, [presetId]: value }
    setKeys(next)
    save({ keys: next })
  }

  function onRememberChange(next: boolean) {
    setRemember(next)
    save({ remember: next })
  }

  function onClearClick() {
    setKeys({})
    setRemember(false)
    onClear()
  }

  return (
    <section aria-label="Model access" className="panel key-panel">
      {notice && <p className="key-notice">{notice}</p>}
      <div className="key-row">
        <div className="key-field key-preset">
          <label htmlFor={`${id}-preset`}>Provider</label>
          <select
            id={`${id}-preset`}
            value={presetId}
            onChange={(e) => onPresetChange(e.target.value)}
          >
            {presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
        <div className="key-field key-secret">
          <label htmlFor={`${id}-secret`}>
            {preset?.kind === 'demo' ? 'Demo code' : `${preset?.label ?? ''} API key`.trim()}
          </label>
          <input
            ref={fieldRef}
            id={`${id}-secret`}
            type="password"
            autoComplete="off"
            value={secret}
            onChange={(e) => onSecretChange(e.target.value)}
          />
        </div>
        <label className="remember-field">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => onRememberChange(e.target.checked)}
          />{' '}
          Remember on this device
        </label>
        <button type="button" className="secondary" onClick={onClearClick}>
          Clear
        </button>
      </div>
      {embeddingModel && (
        <p className="key-note">
          Your key or code only writes the answers. Documents and questions are embedded on the
          server with {embeddingModel}.
        </p>
      )}
    </section>
  )
}
