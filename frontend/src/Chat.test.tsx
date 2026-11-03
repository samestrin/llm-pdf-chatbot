import { fireEvent, render, screen, within } from '@testing-library/react'
import Chat from './Chat'

function ndjson(...events: object[]) {
  return new Response(events.map((e) => `${JSON.stringify(e)}\n`).join(''))
}

function ask(question: string) {
  fireEvent.change(screen.getByLabelText('Ask a question'), { target: { value: question } })
  fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
}

describe('Chat', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('asks the default collection unless another is chosen', async () => {
    const fetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
      Promise.resolve(ndjson({ type: 'done', mode: 'fast', model: 'm', cost: null })),
    )
    vi.stubGlobal('fetch', fetch)
    render(<Chat collections={['other', 'default']} />)

    expect(screen.getByLabelText('Ask in collection')).toHaveValue('default')
    ask('one')
    expect(await screen.findByRole('article', { name: 'Answer' })).toHaveAttribute(
      'aria-busy',
      'false',
    )
    fireEvent.change(screen.getByLabelText('Ask in collection'), { target: { value: 'other' } })
    ask('two')

    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(fetch.mock.calls.map((c) => c[0])).toEqual([
      '/api/collections/default/chat',
      '/api/collections/other/chat',
    ])
  })

  it('shows a refusal, and an error event as an alert', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        ndjson({ type: 'refusal', passages: [] }, { type: 'error', message: 'model failed' }),
      ),
    )
    render(<Chat collections={['default']} />)
    ask('anything')

    const answer = await screen.findByRole('article', { name: 'Answer' })
    expect(await within(answer).findByRole('alert')).toHaveTextContent('model failed')
    expect(answer).toHaveTextContent('The documents do not answer this.')
  })

  it('cannot ask with no collection', () => {
    render(<Chat collections={[]} />)
    fireEvent.change(screen.getByLabelText('Ask a question'), { target: { value: 'q' } })
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled()
    expect(screen.getByText('Upload a document to ask about it.')).toBeInTheDocument()
  })
})
