import { fireEvent, render, screen } from '@testing-library/react'
import type { Preset } from './api'
import KeyPanel from './KeyPanel'

const presets: Preset[] = [
  {
    id: 'openai',
    label: 'OpenAI',
    kind: 'key',
    chat_model: 'gpt-5-nano',
    embedding_model: 'text-embedding-3-small',
  },
  {
    id: 'synthetic',
    label: 'Synthetic',
    kind: 'key',
    chat_model: 'hf:zai-org/GLM-5.3-Flash',
    embedding_model: null,
  },
  {
    id: 'demo',
    label: 'Demo code',
    kind: 'demo',
    chat_model: 'gpt-5-nano',
    embedding_model: 'text-embedding-3-small',
  },
]

function setup(stored = null as null | { preset: string; secret: string; remember: boolean }) {
  const onSave = vi.fn()
  const onClear = vi.fn()
  const onClose = vi.fn()
  render(
    <KeyPanel
      presets={presets}
      stored={stored}
      onSave={onSave}
      onClear={onClear}
      onClose={onClose}
    />,
  )
  return { onSave, onClear, onClose }
}

describe('KeyPanel', () => {
  it('lists the presets GET /presets gave it, by label', () => {
    setup()
    const select = screen.getByLabelText('Provider')
    expect(
      [...select.querySelectorAll('option')].map((o) => o.textContent),
    ).toEqual(['OpenAI', 'Synthetic', 'Demo code'])
  })

  it('asks for an API key for a key preset and a demo code for the demo preset', () => {
    setup()
    expect(screen.getByLabelText('API key')).toBeInTheDocument()
    expect(screen.queryByLabelText('Demo code')).toBeNull()
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    expect(screen.getByLabelText('Demo code')).toBeInTheDocument()
    expect(screen.queryByLabelText('API key')).toBeNull()
  })

  it('saves the trimmed secret with the chosen preset and the remember box', () => {
    const { onSave } = setup()
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: '  sk-k  ' } })
    fireEvent.click(screen.getByLabelText('Remember on this device'))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({ preset: 'openai', secret: 'sk-k' }, true)
  })

  it('will not save an empty secret', () => {
    const { onSave } = setup()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('starts from the stored credentials, with the key not shown as plain text', () => {
    setup({ preset: 'demo', secret: 'demo-code', remember: true })
    expect(screen.getByLabelText('Provider')).toHaveValue('demo')
    const field = screen.getByLabelText('Demo code')
    expect(field).toHaveValue('demo-code')
    expect(field).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('Remember on this device')).toBeChecked()
  })

  it('clear empties the field and reports the clear', () => {
    const { onClear } = setup({ preset: 'openai', secret: 'sk-k', remember: true })
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(onClear).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('API key')).toHaveValue('')
    expect(screen.getByLabelText('Remember on this device')).not.toBeChecked()
  })

  it('says why a preset with no embedding model cannot take uploads', () => {
    setup()
    expect(screen.queryByText(/no embedding model/)).toBeNull()
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'synthetic' } })
    expect(
      screen.getByText('Synthetic has no embedding model, so it cannot take uploads.'),
    ).toBeInTheDocument()
  })

  it('closes without saving', () => {
    const { onClose, onSave } = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onSave).not.toHaveBeenCalled()
  })
})