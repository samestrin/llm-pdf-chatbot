// The demo shows positive-reinforcement training only. A claim that recommends an
// aversive method stops the capture (e2e/live/demo.spec.ts); one that warns against it
// ("never punish") is fine. Tested in e2e/aversive.spec.ts, which CI runs.
export const AVERSIVE =
  /collar (pop|correction|jerk)|leash (pop|correction|jerk)|pop(ping)? the (collar|leash|lead)|\bcorrection|prong|choke (chain|collar)|pinch collar|e-?collar|shock collar|spray bottle|squirt|scruff|\balpha\b|dominan|\bscold|\bsmack|\byank|\bjerk|rub (his|her|their) nose|\bpunish/i
export const NEGATED =
  /\b(avoid|never|don.?t|do not|without|instead of|rather than|not|no need)\b[^.]{0,50}(punish|scold|correction|jerk|yank|alpha|dominan|choke|prong|shock|reprimand)/i

export function aversiveClaims(claims: string[]): string[] {
  return claims.filter((c) => AVERSIVE.test(c) && !NEGATED.test(c))
}
