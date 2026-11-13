import { expect, test } from '@playwright/test'
import { aversiveClaims } from './live/aversive'

// The live demo's screen (e2e/live/aversive.ts). It runs here, with the mocked e2e suite,
// because the live capture itself needs a running instance and is not run in CI.

test('the aversive screen catches punishment advice and passes reward-based advice', () => {
  expect(aversiveClaims(['Give a quick collar correction if the dog does not come.'])).toHaveLength(1)
  expect(aversiveClaims(['Jerk the leash sharply to keep control.'])).toHaveLength(1)
  expect(aversiveClaims(['Use an e-collar for off-leash recall.'])).toHaveLength(1)
  expect(aversiveClaims(['Never punish a dog for growling; it is communication.'])).toEqual([])
  expect(aversiveClaims(['Avoid reprimanding or yelling at the dog.'])).toEqual([])
  expect(aversiveClaims(['Click within 1.3 seconds, then give a treat.'])).toEqual([])
})

// Each clause is checked on its own: a warning in one clause does not excuse advice in
// the next, and a negation covers only what follows it in its own clause.
const RECOMMENDS_AVERSIVE = [
  'Avoid choke chains; use a prong collar.',
  'Avoid choke chains, use a prong collar.',
  'Avoid choke chains and use a prong collar instead.',
  'Never yell at the dog. Give a collar correction instead.',
  'Do not punish growling, but jerk the leash if the dog lunges.',
  'Use a prong collar rather than a choke chain.',
  'Never use a choke chain; a shock collar works better.',
]

const WARNS_AGAINST_AVERSIVE = [
  'Never punish or scold the dog.',
  'Never jerk or yank the leash.',
  'Never jerk and yank the leash.',
  "Don't use a prong collar or an e-collar.",
  'Avoid choke chains, prong collars and shock collars.',
  'Use treats instead of a choke chain.',
  'Reward calm behaviour rather than punishing lunging.',
  'Do not punish growling; reward calm behaviour instead.',
  'There is no need for a prong collar: reward the dog for staying close.',
  'Walk the dog on a flat collar, without a prong collar or a choke chain.',
]

for (const claim of RECOMMENDS_AVERSIVE) {
  test(`the aversive screen catches: ${claim}`, () => {
    expect(aversiveClaims([claim])).toEqual([claim])
  })
}

for (const claim of WARNS_AGAINST_AVERSIVE) {
  test(`the aversive screen passes: ${claim}`, () => {
    expect(aversiveClaims([claim])).toEqual([])
  })
}
