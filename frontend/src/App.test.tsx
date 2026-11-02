import { render, screen } from '@testing-library/react'
import App from './App'

describe('App', () => {
  it('renders the placeholder heading', () => {
    render(<App />)
    expect(
      screen.getByRole('heading', { name: /llm pdf chatbot/i }),
    ).toBeInTheDocument()
  })
})
