import { createRef } from 'react'
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

const NOTICE = 'Public demo: do not upload private files. Uploads are deleted after 45 minutes.'

function setup(
  stored = null as null | {
    preset: string
    keys: Record<string, string>
    remember: boolean
  },
) {
  const onSave = vi.fn()
  const onClear = vi.fn()
  render(
    <KeyPanel presets={presets} notice={NOTICE} stored={stored} onSave={onSave} onClear={onClear} />,
  )
  return { onSave, onClear }
}

describe('KeyPanel', () => {
  it('is an open "Model access" section: the notice first, then the controls in the shell order', () => {
    setup()
    const panel = screen.getByRole('region', { name: 'Model access' })
    expect(panel.tagName).toBe('SECTION')
    expect(panel.firstElementChild).toHaveTextContent(NOTICE)
    expect(panel.firstElementChild).toHaveClass('key-notice')
    expect([...panel.querySelectorAll('select, input, button')]).toEqual([
      screen.getByLabelText('Provider'),
      screen.getByLabelText('OpenAI API key'),
      screen.getByLabelText('Remember on this device'),
      screen.getByRole('button', { name: 'Clear' }),
    ])
    // Saved as it is typed: no Save and no Close.
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull()
  })

  it('renders no notice row when the notice is empty', () => {
    render(<KeyPanel presets={presets} notice="" stored={null} onSave={() => {}} onClear={() => {}} />)
    expect(document.querySelector('.key-notice')).toBeNull()
  })

  it('lists the presets GET /presets gave it, by label', () => {
    setup()
    const select = screen.getByLabelText('Provider')
    expect(
      [...select.querySelectorAll('option')].map((o) => o.textContent),
    ).toEqual(['OpenAI', 'Synthetic', 'Demo code'])
  })

  it('asks for the provider’s API key for a key preset and a demo code for the demo preset', () => {
    setup()
    expect(screen.getByLabelText('OpenAI API key')).toBeInTheDocument()
    expect(screen.queryByLabelText('Demo code')).toBeNull()
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'synthetic' } })
    expect(screen.getByLabelText('Synthetic API key')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    expect(screen.getByLabelText('Demo code')).toBeInTheDocument()
    expect(screen.queryByLabelText(/API key/)).toBeNull()
  })

  it('saves the trimmed secret with the chosen preset as it is typed', () => {
    const { onSave } = setup()
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: '  sk-k  ' } })
    expect(onSave).toHaveBeenLastCalledWith({ preset: 'openai', keys: { openai: 'sk-k' } }, false)
  })

  it('ticking remember saves again with remember on', () => {
    const { onSave } = setup()
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: 'sk-k' } })
    fireEvent.click(screen.getByLabelText('Remember on this device'))
    expect(onSave).toHaveBeenLastCalledWith({ preset: 'openai', keys: { openai: 'sk-k' } }, true)
  })

  it('switching preset saves the new choice and shows that preset’s saved key', () => {
    const { onSave } = setup({
      preset: 'demo',
      keys: { demo: 'demo-code', openai: 'sk-k' },
      remember: true,
    })
    expect(screen.getByLabelText('Demo code')).toHaveValue('demo-code')
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    expect(screen.getByLabelText('OpenAI API key')).toHaveValue('sk-k')
    expect(onSave).toHaveBeenLastCalledWith(
      { preset: 'openai', keys: { demo: 'demo-code', openai: 'sk-k' } },
      true,
    )
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    expect(screen.getByLabelText('Demo code')).toHaveValue('demo-code')
  })

  it('switching to a preset with no saved key shows an empty field', () => {
    setup({ preset: 'demo', keys: { demo: 'demo-code' }, remember: false })
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    expect(screen.getByLabelText('OpenAI API key')).toHaveValue('')
  })

  it('keeps a typed code when switching away and back', () => {
    setup()
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    fireEvent.change(screen.getByLabelText('Demo code'), { target: { value: 'demo-code' } })
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    expect(screen.getByLabelText('OpenAI API key')).toHaveValue('')
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    expect(screen.getByLabelText('Demo code')).toHaveValue('demo-code')
  })

  it('typing one preset’s key keeps every other preset’s key', () => {
    const { onSave } = setup({ preset: 'demo', keys: { demo: 'demo-code' }, remember: true })
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: ' sk-k ' } })
    expect(onSave).toHaveBeenLastCalledWith(
      { preset: 'openai', keys: { demo: 'demo-code', openai: 'sk-k' } },
      true,
    )
  })

  it('never saves a blank secret as a key', () => {
    const { onSave } = setup()
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: '   ' } })
    expect(onSave).toHaveBeenLastCalledWith({ preset: 'openai', keys: {} }, false)
  })

  it('starts from the stored credentials, with the key not shown as plain text', () => {
    setup({ preset: 'demo', keys: { demo: 'demo-code' }, remember: true })
    expect(screen.getByLabelText('Provider')).toHaveValue('demo')
    const field = screen.getByLabelText('Demo code')
    expect(field).toHaveValue('demo-code')
    expect(field).toHaveAttribute('type', 'password')
    expect(field).toHaveAttribute('autocomplete', 'off')
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
    expect(screen.getByLabelText('OpenAI API key')).toHaveValue('')
    expect(screen.getByLabelText('Remember on this device')).not.toBeChecked()
    // Every preset's key is gone, not just the active one's.
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'demo' } })
    expect(screen.getByLabelText('Demo code')).toHaveValue('')
  })

  it('emptying a preset’s field forgets that key and keeps the others', () => {
    const { onSave } = setup({
      preset: 'demo',
      keys: { demo: 'demo-code', openai: 'sk-k' },
      remember: false,
    })
    fireEvent.change(screen.getByLabelText('Demo code'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'openai' } })
    expect(onSave).toHaveBeenLastCalledWith({ preset: 'openai', keys: { openai: 'sk-k' } }, false)
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
        notice={NOTICE}
        stored={null}
        onSave={() => {}}
        onClear={() => {}}
      />,
    )
    expect(
      screen.getByText(
        'Your key or code only writes the answers. Documents and questions are embedded on the server with demo-embedding.',
      ),
    ).toBeInTheDocument()
  })

  it('hands the page its key field, so the page can focus it', () => {
    const ref = createRef<HTMLInputElement>()
    render(
      <KeyPanel
        presets={presets}
        notice={NOTICE}
        stored={null}
        onSave={() => {}}
        onClear={() => {}}
        fieldRef={ref}
      />,
    )
    expect(ref.current).toBe(screen.getByLabelText('OpenAI API key'))
  })
})
