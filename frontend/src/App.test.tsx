import { render, screen } from '@testing-library/react'
import App from './App'

function stubFetch(response: () => Promise<Response>) {
  const fetch = vi.fn(response)
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('App', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('renders the placeholder heading', () => {
    stubFetch(() => Promise.resolve(Response.json({ status: 'ok' })))
    render(<App />)
    expect(
      screen.getByRole('heading', { name: /llm pdf chatbot/i }),
    ).toBeInTheDocument()
  })

  it('asks the backend for its health at same-origin /api/health', async () => {
    const fetch = stubFetch(() => Promise.resolve(Response.json({ status: 'ok' })))
    render(<App />)
    expect(await screen.findByText('Backend: ok')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/health', expect.anything())
  })

  it('shows the backend as unreachable on an error status', async () => {
    stubFetch(() => Promise.resolve(Response.json({ detail: 'down' }, { status: 503 })))
    render(<App />)
    expect(await screen.findByText('Backend: unreachable')).toBeInTheDocument()
  })

  it('shows the backend as unreachable when the request fails', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    render(<App />)
    expect(await screen.findByText('Backend: unreachable')).toBeInTheDocument()
  })
})
