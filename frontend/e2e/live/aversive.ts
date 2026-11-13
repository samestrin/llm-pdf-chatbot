// The demo shows positive-reinforcement training only. A claim that recommends an
// aversive method stops the capture (e2e/live/demo.spec.ts); one that warns against it
// ("never punish") is fine. Tested in e2e/aversive.spec.ts, which CI runs.
const AVERSIVE =
  /collar (pop|correction|jerk)|leash (pop|correction|jerk)|pop(ping)? the (collar|leash|lead)|\bcorrection|prong|choke (chain|collar)|pinch collar|e-?collar|shock collar|spray bottle|squirt|scruff|\balpha\b|dominan|\bscold|\bsmack|\byank|\bjerk|rub (his|her|their) nose|\bpunish/gi

// A negation excuses only the aversive terms that follow it, close by, in its own clause.
const NEGATION = /\b(avoid|never|don.?t|do not|stop|without|instead of|rather than|not|no need)\b/i
const NEGATION_REACH = 50

// Where a clause ends: a sentence or clause mark, "but", or a conjunction or comma that
// starts a new instruction ("..., use a prong collar", "... and try a shock collar").
const CLAUSE_BREAK =
  /[.;:!?]|\bbut\b|(?:,|\b(?:and|then|so|or))\s+(?:instead\s+)?(?=(?:use|try|put|apply|switch|fit|reach|grab|give)\b)/i

function recommendsAversive(clause: string): boolean {
  return [...clause.matchAll(AVERSIVE)].some(
    (m) => !NEGATION.test(clause.slice(Math.max(0, m.index - NEGATION_REACH), m.index)),
  )
}

export function aversiveClaims(claims: string[]): string[] {
  return claims.filter((c) => c.split(CLAUSE_BREAK).some(recommendsAversive))
}
