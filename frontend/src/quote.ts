// Find a cited quote in a page's pdf.js text layer.
//
// The quote comes from the stored page text, which the backend read with its
// own extractor (or OCR), so its spacing, line breaks, hyphenation, case and
// punctuation can all differ from the pdf.js text items. The search compares
// letters and digits only: case-folded, compatibility-decomposed (ligatures
// such as "ﬁ" become "fi") and stripped of accents, so both sides agree
// however the text was written.

const KEEP = /[\p{L}\p{N}]/u
const MARK = /\p{M}/u

/** The searchable characters of one code point (often none, sometimes several). */
function fold(char: string): string {
  let out = ''
  for (const c of char.toLowerCase().normalize('NFKD')) if (KEEP.test(c)) out += c
  return out
}

export function normalise(text: string): string {
  let out = ''
  for (const char of text) out += fold(char)
  return out
}

/** A highlighted part of one text item: `str.slice(start, end)` of item `item`. */
export interface QuoteRange {
  item: number
  start: number
  end: number
}

/**
 * Where `quote` falls in `items` (the page's text items in reading order), as
 * one range per item it touches, or null when it is not on the page.
 */
export function findQuote(items: string[], quote: string): QuoteRange[] | null {
  const needle = normalise(quote)
  if (!needle) return null

  // The normalised page, and for each of its characters the item and the
  // UTF-16 span of the original code point it came from.
  let haystack = ''
  const origin: { item: number; start: number; end: number }[] = []
  items.forEach((str, item) => {
    let offset = 0
    for (const char of str) {
      const folded = fold(char)
      for (let i = 0; i < folded.length; i++)
        origin.push({ item, start: offset, end: offset + char.length })
      haystack += folded
      offset += char.length
    }
  })

  const at = haystack.indexOf(needle)
  if (at < 0) return null

  const ranges: QuoteRange[] = []
  for (const o of origin.slice(at, at + needle.length)) {
    const last = ranges[ranges.length - 1]
    if (last && last.item === o.item) last.end = o.end
    else ranges.push({ ...o })
  }
  // A combining accent after the last matched letter belongs to that letter.
  for (const range of ranges) {
    const str = items[range.item]
    while (range.end < str.length && MARK.test(str[range.end])) range.end++
  }
  return ranges
}
