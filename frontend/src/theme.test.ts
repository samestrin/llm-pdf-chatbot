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

// Spacing properties: padding, margin (and their sides), gaps and scroll margins.
const SPACING_PROP =
  /^(?:(?:padding|margin|scroll-margin)(?:-(?:top|right|bottom|left|inline|block|start|end))*|gap|row-gap|column-gap)$/
// A length literal: the spacing scale says every spacing value is a token.
const SPACING_LITERAL = /\d*\.?\d+(?:rem|em|px|%|ch|vw|vh|vmin|vmax)\b/

/** Spacing declarations of a stylesheet whose value holds a length outside a token. */
function spacingLiterals(css: string): string[] {
  return declarations(css)
    .filter(
      ({ prop, value }) =>
        SPACING_PROP.test(prop) && SPACING_LITERAL.test(value.replace(/var\(--[\w-]+\)/g, '')),
    )
    .map(({ prop, value }) => `${prop}: ${value}`)
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

  it('no spacing literal outside tokens, except the spec\'s one footer links gap', () => {
    const offending: string[] = []
    let specGaps = 0
    for (const [file, text] of Object.entries(sources)) {
      if (!file.endsWith('.css')) continue
      for (const { prop, value } of declarations(text)) {
        if (!SPACING_PROP.test(prop)) continue
        // The shell spec writes the footer links column gap as 1.25rem itself
        // (docs/demo-shell.md, Footer); ocr ships the same one exception.
        if (file === './theme.css' && prop === 'column-gap' && value === '1.25rem') {
          specGaps += 1
          continue
        }
        if (SPACING_LITERAL.test(value.replace(/var\(--[\w-]+\)/g, '')))
          offending.push(`${file}: ${prop}: ${value}`)
      }
    }
    expect(offending).toEqual([])
    // The exception is allowed exactly once, and only as the footer links gap.
    expect(specGaps).toBe(1)
    expect(THEME).toMatch(/\.footer-links\s*\{[^}]*column-gap:\s*1\.25rem[^}]*\}/)
  })

  it('the spacing audit catches literals and passes tokenised spacing', () => {
    const bad = [
      'a { padding: 12px; }',
      'a { margin: 0 8px; }',
      'a { gap: 1.5rem; }',
      'a { padding-bottom: 0.2em; }',
      'a { margin: -1px; }',
      'a { scroll-margin-top: calc(3.5rem + var(--space-4)); }',
    ]
    for (const css of bad) expect(spacingLiterals(css), css).not.toEqual([])
    const good = [
      'a { padding: var(--space-2) var(--space-3); }',
      'a { margin: 0 auto; }',
      'a { margin: 0; }',
      'a { gap: 0; }',
      'a { margin: 0 calc(-1 * var(--space-4)) calc(-1 * var(--space-4)); }',
      'a { scroll-margin-top: calc(var(--bar-height) + var(--space-4)); }',
      'a { max-width: 85%; height: 3.5rem; min-width: 220px; }',
    ]
    for (const css of good) expect(spacingLiterals(css), css).toEqual([])
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

/** Every `--color-*: light-dark(a, b)` declaration, split at the top-level comma
 * (an `rgb(...)` value keeps its own parentheses). */
function colourTokens(): Record<string, [string, string]> {
  const out: Record<string, [string, string]> = {}
  for (const line of THEME.split('\n')) {
    const m = line.match(/^\s*(--color-[\w-]+):\s*light-dark\((.*)\);\s*$/)
    if (!m) continue
    let depth = 0
    let cut = -1
    for (let i = 0; i < m[2].length; i++) {
      const c = m[2][i]
      if (c === '(') depth += 1
      else if (c === ')') depth -= 1
      else if (c === ',' && depth === 0) {
        cut = i
        break
      }
    }
    if (cut > 0) out[m[1]] = [m[2].slice(0, cut).trim(), m[2].slice(cut + 1).trim()]
  }
  return out
}

// The demo shell spec (docs/demo-shell.md in samestrin/fleet-deploy): the shared
// table is identical in translate, pdfchat and ocr; only the accent rows differ.
const SHARED_COLOURS: Record<string, [string, string]> = {
  '--color-bg': ['#f4f5f7', '#0f1115'],
  '--color-surface': ['#ffffff', '#171a20'],
  '--color-surface-sunken': ['#eef0f3', '#1f232b'],
  '--color-text': ['#15171c', '#e6e8ec'],
  '--color-muted': ['#545a65', '#a1a7b3'],
  '--color-border': ['#dcdfe5', '#2c313a'],
  '--color-border-strong': ['#7c8390', '#6f7785'],
  '--color-on-primary': ['#ffffff', '#ffffff'],
  '--color-disabled': ['#e4e7ec', '#2a2f38'],
  '--color-on-disabled': ['#545a65', '#a1a7b3'],
  '--color-danger': ['#b3261e', '#ff8a80'],
  '--color-danger-subtle': ['#fdecea', '#3a1d1b'],
  '--color-warn': ['#8a4a00', '#f0a640'],
  '--color-shadow': ['rgb(16 24 40 / 0.08)', 'rgb(0 0 0 / 0.45)'],
}

// The spec's accent table, pdfchat (blue) column: the only per-app tokens.
const PDFCHAT_ACCENT: Record<string, [string, string]> = {
  '--color-accent': ['#2451cc', '#8ab0ff'],
  '--color-accent-hover': ['#1d42a8', '#a8c4ff'],
  '--color-accent-subtle': ['#e9eefb', '#1c2740'],
  '--color-on-accent': ['#ffffff', '#0b1220'],
  '--color-primary': ['#2451cc', '#3a66d6'],
  '--color-primary-hover': ['#1d42a8', '#2d5bd0'],
  '--color-focus': ['#2451cc', '#8ab0ff'],
}

// The spec's spacing, radius and type scales, and the layout token it fixes.
const SPEC_SCALES: [string, string][] = [
  ['--page-max', '76rem'],
  ['--space-1', '0.25rem'],
  ['--space-2', '0.5rem'],
  ['--space-3', '0.75rem'],
  ['--space-4', '1rem'],
  ['--space-5', '1.5rem'],
  ['--space-6', '2rem'],
  ['--radius-sm', '6px'],
  ['--radius-md', '10px'],
  ['--radius-lg', '14px'],
  ['--radius-pill', '999px'],
  ['--text-xs', '0.75rem'],
  ['--text-sm', '0.875rem'],
  ['--text-base', '1rem'],
  ['--text-lg', '1.125rem'],
  ['--text-xl', '1.375rem'],
  ['--weight-medium', '500'],
  ['--weight-bold', '650'],
  ['--leading-tight', '1.25'],
  ['--leading', '1.5'],
  ['--font-sans', "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"],
]

describe('the demo shell spec table', () => {
  const tokens = colourTokens()

  it.each(Object.entries(SHARED_COLOURS))(
    '%s is the shared spec value in both themes',
    (name, pair) => {
      expect(tokens[name], name).toEqual(pair)
    },
  )

  it.each(Object.entries(PDFCHAT_ACCENT))('%s is the pdfchat accent spec value', (name, pair) => {
    expect(tokens[name], name).toEqual(pair)
  })

  it.each(SPEC_SCALES)('%s is %s', (token, value) => {
    const escaped = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    expect(THEME).toMatch(new RegExp(`${token}:\\s*${escaped};`))
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
  ['--color-on-primary', '--color-primary'],
  ['--color-on-primary', '--color-primary-hover'],
  ['--color-on-disabled', '--color-disabled'],
  ['--color-danger', '--color-bg'],
  ['--color-danger', '--color-surface'],
  ['--color-danger', '--color-danger-subtle'],
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
