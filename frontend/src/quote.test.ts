import { findQuote, normalise } from './quote'

describe('normalise', () => {
  it('keeps only letters and digits, case-folded', () => {
    expect(normalise('  The  “Quick”\nbrown—fox, 12%! ')).toBe('thequickbrownfox12')
  })

  it('expands ligatures and compatibility forms', () => {
    expect(normalise('ﬁnal ①')).toBe('final1')
  })
})

describe('findQuote', () => {
  it('finds a quote inside one text item', () => {
    expect(findQuote(['The quick brown fox jumps.'], 'brown fox')).toEqual([
      { item: 0, start: 10, end: 19 },
    ])
  })

  it('finds a quote across items despite spacing, case, line breaks and hyphens', () => {
    const items = ['Revenue grew twelve per-', '', 'cent in the  third', 'quarter.']
    expect(findQuote(items, 'GREW twelve percent in the third\nquarter')).toEqual([
      { item: 0, start: 8, end: 23 },
      { item: 2, start: 0, end: 18 },
      { item: 3, start: 0, end: 7 },
    ])
  })

  it('maps a ligature in the text layer back to its one character', () => {
    expect(findQuote(['the ﬁnal word'], 'final')).toEqual([{ item: 0, start: 4, end: 8 }])
  })

  it('counts characters outside the BMP as their UTF-16 length', () => {
    expect(findQuote(['𝐀 then fox'], 'then fox')).toEqual([{ item: 0, start: 3, end: 11 }])
  })

  it('matches accented and unaccented, precomposed and combining forms', () => {
    expect(findQuote(['cafe\u0301 au lait'], 'Café')).toEqual([{ item: 0, start: 0, end: 5 }])
    expect(findQuote(['Café au lait'], 'cafe')).toEqual([{ item: 0, start: 0, end: 4 }])
  })

  it('returns null when the quote is not on the page', () => {
    expect(findQuote(['The quick brown fox.'], 'lazy dog')).toBeNull()
  })

  it('returns null for a quote with nothing searchable in it', () => {
    expect(findQuote(['- -- ...'], '...')).toBeNull()
    expect(findQuote(['anything'], '')).toBeNull()
  })
})
