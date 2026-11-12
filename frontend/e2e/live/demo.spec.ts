import { writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test, type Page } from '@playwright/test'
import gifenc from 'gifenc'
import { PNG } from 'pngjs'
import { README_IMAGES } from '../readme-images'

const { GIFEncoder, applyPalette, quantize } = gifenc

const OUT = fileURLToPath(new URL('../../../docs/img/', import.meta.url))
const COLLECTION = process.env.LIVE_COLLECTION ?? 'dog-training'
const FAST_QUESTION = process.env.LIVE_FAST_QUESTION ?? 'How do I use a clicker to train my dog?'
const DEEP_QUESTION =
  process.env.LIVE_DEEP_QUESTION ?? 'My dog barks and lunges at other dogs on walks. How do I fix it?'
const REFUSAL_QUESTION = process.env.LIVE_REFUSAL_QUESTION ?? 'How do I teach my parrot to talk?'

// The demo shows positive-reinforcement training only. A claim that recommends an
// aversive method stops the capture; one that warns against it ("never punish") is fine.
export const AVERSIVE =
  /collar (pop|correction|jerk)|leash (pop|correction|jerk)|pop(ping)? the (collar|leash|lead)|\bcorrection|prong|choke (chain|collar)|pinch collar|e-?collar|shock collar|spray bottle|squirt|scruff|\balpha\b|dominan|\bscold|\bsmack|\byank|\bjerk|rub (his|her|their) nose|\bpunish/i
export const NEGATED =
  /\b(avoid|never|don.?t|do not|without|instead of|rather than|not|no need)\b[^.]{0,50}(punish|scold|correction|jerk|yank|alpha|dominan|choke|prong|shock|reprimand)/i

export function aversiveClaims(claims: string[]): string[] {
  return claims.filter((c) => AVERSIVE.test(c) && !NEGATED.test(c))
}

type Frame = { png: Buffer; delay: number }

test('the aversive screen catches punishment advice and passes reward-based advice', () => {
  expect(aversiveClaims(['Give a quick collar correction if the dog does not come.'])).toHaveLength(1)
  expect(aversiveClaims(['Jerk the leash sharply to keep control.'])).toHaveLength(1)
  expect(aversiveClaims(['Use an e-collar for off-leash recall.'])).toHaveLength(1)
  expect(aversiveClaims(['Never punish a dog for growling; it is communication.'])).toEqual([])
  expect(aversiveClaims(['Avoid reprimanding or yelling at the dog.'])).toEqual([])
  expect(aversiveClaims(['Click within 1.3 seconds, then give a treat.'])).toEqual([])
})


async function openCollection(page: Page) {
  await page.goto('/')
  await page.getByLabel('Collection').selectOption(COLLECTION)
  await expect(page.getByText(/^Ready: /).first()).toBeVisible()
}

async function waitForAnswer(page: Page) {
  const answer = page.getByRole('article', { name: 'Answer' }).last()
  await expect(answer).toHaveAttribute('aria-busy', 'false', { timeout: 5 * 60 * 1000 })
  await answer.scrollIntoViewIfNeeded()
  // Bring the chat panel's answer into view, then keep the page itself at the top so
  // the header stays in the frame.
  await page.evaluate(() => window.scrollTo(0, 0))
  return answer
}

async function claimsOf(page: Page) {
  return page.getByRole('article', { name: 'Answer' }).last().getByRole('list', { name: 'Claims' }).getByRole('listitem').allInnerTexts()
}

test('records the README demo GIF from a live instance', async ({ page }) => {
  const frames: Frame[] = []
  const snap = async (delay: number) => frames.push({ png: await page.screenshot(), delay })

  // 1. The collection, every document ready.
  await openCollection(page)
  await snap(2000)

  // 2. Fast mode.
  await page.getByLabel('Ask a question').fill(FAST_QUESTION)
  await snap(1500)
  await page.getByRole('button', { name: 'Ask' }).click()
  await waitForAnswer(page)
  const fast = await claimsOf(page)
  expect(fast.length, 'the fast answer has claims').toBeGreaterThan(0)
  await snap(4500)

  // 3. Deep mode: the plan's steps, then the answer.
  await openCollection(page)
  await page.getByLabel('Deep mode').check()
  await page.getByLabel('Ask a question').fill(DEEP_QUESTION)
  await snap(1500)
  await page.getByRole('button', { name: 'Ask' }).click()
  await page.getByRole('list', { name: 'Steps' }).waitFor({ timeout: 5 * 60 * 1000 })
  await snap(2000)
  await waitForAnswer(page)
  const deep = await claimsOf(page)
  expect(deep.length, 'the deep answer has claims').toBeGreaterThan(0)
  await snap(5000)

  // 4. A question the documents do not answer: refused, with the closest passages.
  await openCollection(page)
  await page.getByLabel('Ask a question').fill(REFUSAL_QUESTION)
  await page.getByRole('button', { name: 'Ask' }).click()
  await waitForAnswer(page)
  await expect(page.getByRole('list', { name: 'Closest passages' })).toBeVisible()
  await snap(3500)

  const bad = aversiveClaims([...fast, ...deep])
  expect(bad, 'no claim recommends an aversive method').toEqual([])

  const sizes = new Set(frames.map(({ png }) => { const { width, height } = PNG.sync.read(png); return `${width}x${height}` }))
  if (sizes.size !== 1) throw new Error(`GIF frames differ in size (${[...sizes].join(', ')}); refusing to write the GIF`)

  const gif = GIFEncoder()
  for (const { png, delay } of frames) {
    const { data, width, height } = PNG.sync.read(png)
    const palette = quantize(data, 256)
    gif.writeFrame(applyPalette(data, palette), width, height, { palette, delay })
  }
  gif.finish()
  writeFileSync(OUT + README_IMAGES.gif, gif.bytes())
})
