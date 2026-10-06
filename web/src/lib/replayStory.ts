/**
 * The one-line caption under the homepage replay: what the telescope is doing at this moment, in
 * plain words, for a visitor who has never heard of the task. Pure, so the rules can be tested.
 */
export type StoryKey =
  | 'loading' | 'observe' | 'observe_required' | 'observe_blocked'
  | 'dawn' | 'day' | 'dusk' | 'closed' | 'wait' | 'done'

export interface StoryInput {
  loaded: boolean
  ended: boolean
  kind: 'observe' | 'gap'
  /** Position inside the current beat, 0…1. */
  frac: number
  /** The beat is a daytime being time-lapsed. */
  lapse: boolean
  sunUp: boolean
  open: boolean
  /** The exposure on screen: targets its fibres landed on, how many of those are must-dos, points earned, which pointing this is. */
  action: { hits: number; required: number; score: number; ordinal: number } | null
  /** The night this beat belongs to (for dusk: the night about to start). */
  nightNo: number
  nights: number
  totals: { done: number; total: number; score: number }
}

export interface Story { key: StoryKey; params: Record<string, string | number> }

/**
 * Whether an exposure counts as having observed its targets on screen. An exposure the scorer marks
 * finished but pays nothing for — the dome was shut, so no light reached the fibres — does not light
 * them: the caption says it captured nothing, and the picture has to agree.
 */
export function lightsTargets(a: { a: string; cls: string; score: number; penalty: number }): boolean {
  return a.a === 'observe' && a.cls === 'completed' && a.score - a.penalty > 0
}

/**
 * One exposure's beat on screen: the telescope swings over until SWING_END, its fibres reach out until
 * LAND_PHASE, and from that moment the exposure counts — its targets light, the "+score" rises and the
 * score readout ticks — while the picture holds still for the rest of the beat.
 */
export const SWING_END = 0.35
export const LAND_PHASE = 0.55

/**
 * How many actions the picture should treat as finished. The run's clock only finishes an exposure at the
 * end of its beat, which left the score and the lit targets one exposure behind the beam and the caption
 * (the caption said +14.8 while +11.2 from the previous pointing was rising on screen). Counting the live
 * exposure from the moment its fibres land keeps all of them on the same pointing.
 */
export function shownSettled(settled: number, live: { index: number; phase: number } | null): number {
  if (!live || live.phase < LAND_PHASE) return settled
  return Math.max(settled, live.index + 1)
}

const one = (v: number) => (Math.round(v * 10) / 10).toFixed(1)

export function storyFor(s: StoryInput): Story {
  const base = { night: s.nightNo, nights: s.nights }
  if (!s.loaded) return { key: 'loading', params: base }
  if (s.ended) return { key: 'done', params: { ...base, done: s.totals.done, total: s.totals.total, score: one(s.totals.score) } }
  if (s.kind === 'observe' && s.action) {
    const params = { ...base, n: s.action.hits, r: s.action.required, score: one(s.action.score), k: s.action.ordinal }
    if (s.action.score <= 0 && !s.open) return { key: 'observe_blocked', params }
    return { key: s.action.required > 0 ? 'observe_required' : 'observe', params }
  }
  if (s.lapse) {
    if (s.sunUp) return { key: 'day', params: base }
    return { key: s.frac < 0.5 ? 'dawn' : 'dusk', params: base }
  }
  return { key: s.open ? 'wait' : 'closed', params: base }
}
