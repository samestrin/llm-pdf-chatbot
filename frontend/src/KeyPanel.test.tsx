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

function setup(
  stored = null as null | {
    preset: string
    keys: Record<string, string>
    remember: boolean
  },
) {
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
    expect(onSave).toHaveBeenCalledWith({ preset: 'openai', keys: { openai: 'sk-k' } }, true)
  })

  it('switching preset shows that preset’s saved key, and switching back restores it', () => {
    setup({
      preset: 'demo',
      keys: { demo: 'demo-code', openai: 'sk-k' },
      remember: true,
    })
    expect(screen.getByLabelText('Demo code')).toHaveValue('demo-code')
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    expect(screen.getByLabelText('API key')).toHaveValue('sk-k')
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    expect(screen.getByLabelText('Demo code')).toHaveValue('demo-code')
  })

  it('switching to a preset with no saved key shows an empty field', () => {
    setup({ preset: 'demo', keys: { demo: 'demo-code' }, remember: false })
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    expect(screen.getByLabelText('API key')).toHaveValue('')
  })

  it('keeps a typed but unsaved code when switching away and back', () => {
    setup()
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    fireEvent.change(screen.getByLabelText('Demo code'), { target: { value: 'demo-code' } })
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    expect(screen.getByLabelText('API key')).toHaveValue('')
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    expect(screen.getByLabelText('Demo code')).toHaveValue('demo-code')
  })

  it('saving one preset’s key keeps every other preset’s key', () => {
    const { onSave } = setup({ preset: 'demo', keys: { demo: 'demo-code' }, remember: true })
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: ' sk-k ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith(
      { preset: 'openai', keys: { demo: 'demo-code', openai: 'sk-k' } },
      true,
    )
  })

  it('will not save an empty secret', () => {
    const { onSave } = setup()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('starts from the stored credentials, with the key not shown as plain text', () => {
    setup({ preset: 'demo', keys: { demo: 'demo-code' }, remember: true })
    expect(screen.getByLabelText('Provider')).toHaveValue('demo')
    const field = screen.getByLabelText('Demo code')
    expect(field).toHaveValue('demo-code')
    expect(field).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('Remember on this device')).toBeChecked()
  })

  it('clear empties the field and reports the clear', () => {
    const { onClear } = setup({
      preset: 'openai',
      keys: { openai: 'sk-k', demo: 'demo-code' },
      remember: true,
    })
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(onClear).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('API key')).toHaveValue('')
    expect(screen.getByLabelText('Remember on this device')).not.toBeChecked()
    // Every preset's key is gone, not just the active one's.
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    expect(screen.getByLabelText('Demo code')).toHaveValue('')
  })

  it('never says a preset cannot take uploads: the server embeds for every preset', () => {
    setup()
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'synthetic' } })
    expect(screen.queryByText(/no embedding model|cannot take uploads/)).toBeNull()
  })

  it('says the key only writes answers, and names the server’s embedding model', () => {
    render(
      <KeyPanel
        presets={presets}
        embeddingModel="demo-embedding"
        stored={null}
        onSave={() => {}}
        onClear={() => {}}
        onClose={() => {}}
      />,
    )
    expect(
      screen.getByText(
        'Your key or code only writes the answers. Documents and questions are embedded on the server with demo-embedding.',
      ),
    ).toBeInTheDocument()
  })

  it('closes without saving', () => {
    const { onClose, onSave } = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onSave).not.toHaveBeenCalled()
  })
})