/// <reference types="node" />
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// The design tokens in theme.css: one set, used everywhere, readable in both themes.
// Read from disk: Vitest turns a CSS import into an empty module. Vitest runs in frontend/.
const dir = join(process.cwd(), 'src')
const sources: Record<string, string> = Object.fromEntries(
  readdirSync(dir)
    .filter((f) => /\.(css|tsx?)$/.test(f) && !/\.test\.tsx?$/.test(f) && f !== 'setupTests.ts')
    .map((f) => [`./${f}`, readFileSync(join(dir, f), 'utf8')]),
)

const THEME = sources['./theme.css']

// Hex colours, colour functions and the named colours a stylesheet might use.
const COLOUR_LITERAL =
  /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(|(?<![\w-])(?:white|black|red|green|blue|yellow|orange|gray|grey|silver|navy|purple)(?![\w-])/i

/** `--color-*` declarations of theme.css: the only place a colour is written down. */
function isColourToken(line: string): boolean {
  return /^\s*--color-[\w-]+:/.test(line)
}

/** `prop: value` declarations of a stylesheet, without comments. */
function declarations(css: string): { prop: string; value: string }[] {
  const body = css.replace(/\/\*[\s\S]*?\*\//g, '')
  return [...body.matchAll(/([\w-]+)\s*:\s*([^;{}]+);/g)].map(([, prop, value]) => ({
    prop,
    value: value.trim(),
  }))
}

describe('design tokens', () => {
  it('finds the stylesheets and components', () => {
    expect(THEME).toBeDefined()
    expect(Object.keys(sources)).toEqual(
      expect.arrayContaining(['./PdfViewer.css', './App.tsx', './Chat.tsx', './PdfViewer.tsx']),
    )
  })

  it('no component has a hard-coded colour', () => {
    const offending: string[] = []
    for (const [file, text] of Object.entries(sources)) {
      text.split('\n').forEach((line, i) => {
        if (file === './theme.css' && isColourToken(line)) return
        const code = file.endsWith('.css') ? line : line.replace(/\/\/.*$/, '')
        if (COLOUR_LITERAL.test(code.replace(/\/\*.*?\*\//g, '')))
          offending.push(`${file}:${i + 1}: ${line.trim()}`)
      })
    }
    expect(offending).toEqual([])
  })

  it('radius, elevation, type size, font and focus ring come from tokens', () => {
    const tokenised = /^(border-radius|box-shadow|font-size|font-family|outline|outline-offset)$/
    const allowed = /^(0|none|inherit|unset)$/
    const offending: string[] = []
    for (const [file, text] of Object.entries(sources)) {
      if (!file.endsWith('.css')) continue
      for (const { prop, value } of declarations(text)) {
        if (tokenised.test(prop) && !allowed.test(value) && !value.includes('var(--'))
          offending.push(`${file}: ${prop}: ${value}`)
      }
    }
    expect(offending).toEqual([])
  })

  it('declares every colour token once, with a light and a dark value', () => {
    const names = THEME.split('\n')
      .filter(isColourToken)
      .map((line) => line.trim().split(':')[0])
    expect(names.length).toBeGreaterThan(0)
    expect(new Set(names).size).toBe(names.length)
  })

  it('declares the spacing, type, radius, elevation and focus scales', () => {
    for (const token of [
      '--space-1',
      '--space-4',
      '--text-sm',
      '--text-base',
      '--text-xl',
      '--radius-sm',
      '--radius-md',
      '--shadow-1',
      '--focus-ring',
    ])
      expect(THEME).toMatch(new RegExp(`${token}:`))
  })
})

// WCAG 2.x relative luminance and contrast ratio.
function luminance(hex: string): number {
  const v = hex.replace('#', '')
  const full = v.length === 3 ? [...v].map((c) => c + c).join('') : v
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255)
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** `--color-name` -> [light, dark] for the `light-dark(#..., #...)` tokens. */
function palette(): Record<string, [string, string]> {
  const out: Record<string, [string, string]> = {}
  for (const m of THEME.matchAll(
    /(--color-[\w-]+):\s*light-dark\(\s*(#[0-9a-f]{3,6})\s*,\s*(#[0-9a-f]{3,6})\s*\)/gi,
  ))
    out[m[1]] = [m[2], m[3]]
  return out
}

// Text colour on the backgrounds it is drawn on.
const TEXT_PAIRS: [string, string][] = [
  ['--color-text', '--color-bg'],
  ['--color-text', '--color-surface'],
  ['--color-text', '--color-surface-sunken'],
  ['--color-muted', '--color-bg'],
  ['--color-muted', '--color-surface'],
  ['--color-muted', '--color-surface-sunken'],
  ['--color-accent', '--color-surface'],
  ['--color-accent', '--color-bg'],
  ['--color-accent', '--color-accent-subtle'],
  ['--color-on-accent', '--color-accent'],
  ['--color-on-accent', '--color-accent-hover'],
  ['--color-danger', '--color-bg'],
  ['--color-danger', '--color-surface'],
  ['--color-warn', '--color-surface'],
]

// Non-text contrast (WCAG 1.4.11): focus ring and control borders.
const UI_PAIRS: [string, string][] = [
  ['--color-focus', '--color-bg'],
  ['--color-focus', '--color-surface'],
  ['--color-border-strong', '--color-surface'],
]

describe.each([
  ['light', 0],
  ['dark', 1],
] as const)('the %s theme', (_, side) => {
  const colours = palette()

  it.each(TEXT_PAIRS)('%s on %s passes WCAG AA text contrast (4.5:1)', (fg, bg) => {
    expect(colours[fg], fg).toBeDefined()
    expect(colours[bg], bg).toBeDefined()
    expect(contrast(colours[fg][side], colours[bg][side])).toBeGreaterThanOrEqual(4.5)
  })

  it.each(UI_PAIRS)('%s on %s passes non-text contrast (3:1)', (fg, bg) => {
    expect(colours[fg], fg).toBeDefined()
    expect(colours[bg], bg).toBeDefined()
    expect(contrast(colours[fg][side], colours[bg][side])).toBeGreaterThanOrEqual(3)
  })
})
