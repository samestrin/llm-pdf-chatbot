import { fireEvent, render, screen, within } from '@testing-library/react'
import { getDocument } from 'pdfjs-dist'
import PdfViewer from './PdfViewer'

// pdf.js needs a real canvas and worker; the tests script `getDocument` instead.
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: vi.fn(),
  TextLayer: class {
    textContentItemsStr: string[] = []
    textDivs: HTMLElement[] = []
    render = () => Promise.resolve()
    cancel = () => {}
  },
}))

const citation = {
  document: { id: 'd1', filename: 'report.pdf' },
  page: 2,
  quote: 'twelve percent',
}

const viewport = { width: 612, height: 792 }
const pdf = {
  numPages: 2,
  getPage: () =>
    Promise.resolve({
      getViewport: () => viewport,
      render: () => ({ promise: Promise.resolve() }),
      streamTextContent: () => new ReadableStream(),
    }),
}

function task(promise: Promise<unknown>) {
  return { promise, destroy: () => Promise.resolve() } as unknown as ReturnType<typeof getDocument>
}

describe('PdfViewer', () => {
  beforeEach(() => vi.mocked(getDocument).mockReset())

  it('says the page is opening while the PDF loads', () => {
    vi.mocked(getDocument).mockReturnValue(task(new Promise(() => {})))
    render(<PdfViewer citation={citation} onClose={() => {}} />)

    const dialog = screen.getByRole('dialog', { name: 'report.pdf, page 2' })
    expect(within(dialog).getByText('Opening page 2…')).toBeInTheDocument()
    expect(dialog).toHaveAttribute('aria-busy', 'true')
    expect(within(dialog).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('retries a PDF that failed to open', async () => {
    vi.mocked(getDocument)
      .mockReturnValueOnce(task(Promise.reject(new Error('file not found'))))
      .mockReturnValueOnce(task(Promise.resolve(pdf)))
    render(<PdfViewer citation={citation} onClose={() => {}} />)

    const dialog = screen.getByRole('dialog', { name: 'report.pdf, page 2' })
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Could not open report.pdf: file not found',
    )
    expect(dialog).toHaveAttribute('aria-busy', 'false')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }))

    expect(await within(dialog).findByText('Page 2 of 2')).toBeInTheDocument()
    expect(getDocument).toHaveBeenCalledTimes(2)
    expect(within(dialog).queryByRole('alert')).toBeNull()
    expect(within(dialog).queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('takes focus when it opens and closes on Escape', () => {
    vi.mocked(getDocument).mockReturnValue(task(new Promise(() => {})))
    const onClose = vi.fn()
    render(<PdfViewer citation={citation} onClose={onClose} />)

    const dialog = screen.getByRole('dialog', { name: 'report.pdf, page 2' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Enter' })
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('keeps Tab and Shift+Tab inside the viewer', async () => {
    vi.mocked(getDocument).mockReturnValue(task(Promise.reject(new Error('file not found'))))
    render(<PdfViewer citation={citation} onClose={() => {}} />)
    const dialog = screen.getByRole('dialog', { name: 'report.pdf, page 2' })
    const retry = await within(dialog).findByRole('button', { name: 'Retry' })
    const close = within(dialog).getByRole('button', { name: 'Close' })

    retry.focus()
    expect(fireEvent.keyDown(retry, { key: 'Tab' })).toBe(false)
    expect(close).toHaveFocus()
    expect(fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })).toBe(false)
    expect(retry).toHaveFocus()
    // Tab from any control but the last is left to the browser.
    close.focus()
    expect(fireEvent.keyDown(close, { key: 'Tab' })).toBe(true)
    expect(fireEvent.keyDown(close, { key: 'Escape' })).toBe(false)
    // From the dialog itself, Shift+Tab goes round to the last control.
    dialog.focus()
    expect(fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })).toBe(false)
    expect(retry).toHaveFocus()
  })
})
