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
