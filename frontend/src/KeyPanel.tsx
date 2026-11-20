import { useId, useState, type FormEvent } from 'react'
import type { Preset } from './api'
import type { ModelCredentials, StoredCredentials } from './modelAccess'

/**
 * The key panel of a public demo: a preset dropdown fed by `GET /presets`, the
 * visitor's own key or a demo code, a remember box and a clear button. The secret
 * is saved by the page (session storage, or local storage with "remember") and
 * sent only as a request header; Clear wipes it from both stores.
 */
export default function KeyPanel({
  presets,
  embeddingModel = null,
  stored,
  onSave,
  onClear,
  onClose,
}: {
  presets: Preset[]
  /** The model the server embeds every document and question with; null when none. */
  embeddingModel?: string | null
  stored: StoredCredentials | null
  onSave: (credentials: ModelCredentials, remember: boolean) => void
  onClear: () => void
  onClose: () => void
}) {
  const id = useId()
  const [presetId, setPresetId] = useState(stored?.preset ?? presets[0]?.id ?? '')
  const [secret, setSecret] = useState(stored?.secret ?? '')
  const [remember, setRemember] = useState(stored?.remember ?? false)
  const preset = presets.find((p) => p.id === presetId)
  const isDemo = preset?.kind === 'demo'

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = secret.trim()
    if (!trimmed || !preset) return
    onSave({ preset: preset.id, secret: trimmed }, remember)
  }

  function onClearClick() {
    setSecret('')
    setRemember(false)
    onClear()
  }

  return (
    <section aria-labelledby={`${id}-heading`} className="panel key-panel">
      <h2 id={`${id}-heading`}>Model key</h2>
      <p>
        Every answer and upload uses a model. Bring your own key for a provider, or use a demo
        code. The key stays in this browser and is sent only with your requests.
      </p>
      <form onSubmit={onSubmit}>
        <label htmlFor={`${id}-preset`}>Provider</label>
        <select
          id={`${id}-preset`}
          value={presetId}
          onChange={(e) => setPresetId(e.target.value)}
        >
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
        <label htmlFor={`${id}-secret`}>{isDemo ? 'Demo code' : 'API key'}</label>
        <input
          id={`${id}-secret`}
          type="password"
          autoComplete="off"
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
        />
        <label className="remember-field">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
          />{' '}
          Remember on this device
        </label>
        {embeddingModel && (
          <p className="key-note">
            Your key or code only writes the answers. Documents and questions are embedded on the
            server with {embeddingModel}.
          </p>
        )}
        <div className="key-panel-buttons">
          <button type="submit" disabled={!secret.trim() || !preset}>
            Save
          </button>
          <button type="button" className="secondary" onClick={onClearClick}>
            Clear
          </button>
          <button type="button" className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </form>
    </section>
  )
}