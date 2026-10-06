import { onMounted, onUnmounted, reactive, readonly } from 'vue'
import type { OutcomeClass } from '../lib/report'
import { prefersReducedMotion, type SkySite, type SkyTarget } from '../lib/skymap'

/**
 * Shared clock for the bundled organizer example. Participant traces must never
 * be loaded into the public homepage, even for a leaderboard winner.
 * Progress maps onto events so long runs of waiting remain legible.
 * Instead each exposure now owns an equal, legible share of the loop, and each run of waiting between two
 * exposures — three slots or six hundred — collapses into one short beat that shows the sim time just
 * before the next exposure starts. The result is a steady pace where something visible happens throughout.
 */
export interface ReplaySlot { slot: string; night: string; t: string; startSec: number; open: boolean; seeing: number; transp: number; sky: number; eff: number }
export interface ReplayAction {
  i: string; slot: string; a: 'observe' | 'wait'; targets: string[]; center: { ra: number; dec: number } | null
  program: string; cls: OutcomeClass; t: string; dt: number; score: number; penalty: number; startSec: number; doneSec: number
}
export interface RawReplayTarget { id: string; ra: number; dec: number; required: boolean }
export interface RawReplaySlot { slot: string; night: string; t: string; open: boolean; seeing: number; transp: number; sky: number; eff: number }
export interface RawReplayAction {
  i: string; slot: string; a: string; targets: string[]; center: { ra: number; dec: number } | null
  program: string; cls: string; t: string; dt: number; score: number; penalty: number
}
export interface RawReplay {
  site: SkySite
  /** Point sources on the sky, each landed on by one fibre of some pointing; replaces the old fixed-patch "tiles". */
  targets: RawReplayTarget[]
  weather: RawReplaySlot[]
  actions: RawReplayAction[]
  score: { total: number; base_science: number }
  completed: number
  required_missing: string[]
  nights: number
}

export const SLOT_SECONDS = 900

/** One exposure's share of the loop, and the share a whole run of waiting collapses into. */
const OBSERVE_UNIT = 1
const GAP_UNIT = 0.3
/**
 * Extra share a gap earns per full turn the sky has to travel across it. A gap over the daylight hours
 * moves the sky most of the way round, and it needs room to do that at a speed the eye can follow.
 */
const GAP_SWEEP_UNIT = 0.9
/**
 * How far the sky may travel across a gap before travelling stops being the right idea. A few degrees
 * glide by unnoticed. Half a turn cannot: given a beat short enough to keep the replay moving, it
 * crosses the whole map in under a second, and reads as a jolt rather than as time passing. Past this
 * the cursor dissolves instead, and comes back where the night resumes.
 */
const SWEEP_MAX_DEG = 18
/** Share a dissolving gap earns — enough for the cursor to fade out and back without feeling rushed. */
const GAP_DISSOLVE_UNIT = 0.7
/**
 * A gap of this many hours or more is a daytime (or longer) being skipped. The 3D sky plays it as a
 * time-lapse — the sun crossing, the sky brightening and darkening — which needs a few seconds to read.
 */
const DAY_GAP_HOURS = 4
const DAY_LAPSE_UNIT = 3.2
/** Fraction of a dissolving gap spent fading at each end; the middle is held empty. */
const DISSOLVE_EDGE = 0.34
/**
 * Above this many exposures, giving each one its own beat stops working: the loop is capped at a few
 * minutes, so a run of several thousand exposures would hand each beat a frame or two, and every
 * skip between them would land as a jump however it was dressed up. Past this the replay switches to
 * running the clock at a steady rate instead — the sky turns evenly and tiles light as their moment
 * arrives, which cannot jump because nothing is ever cut.
 */
const DENSE_EXPOSURES = 400
/** Real time one turn of the sky is given in that steady mode: slow enough to read as motion. */
const MS_PER_TURN = 3_000
/**
 * Fastest the sky may turn before the cursor marking "now" stops being worth drawing. The reference
 * scenario runs half a year: a hundred and eighty turns will not fit inside a two-minute loop at any
 * speed the eye can follow, and a cursor whipping round several times a second tells a viewer nothing
 * it does not already know from the clock. Past this it is left out, and the tiles carry the replay.
 */
const MAX_CURSOR_DEG_PER_FRAME = 2.5
/**
 * How long the finished picture stays up before the loop starts over. Without it the last exposure
 * never got a frame: the loop wrapped a hair before the final tile lit, so nobody saw the run complete.
 */
const END_HOLD_MS = 2_500
/**
 * How long, in real time, an exposure counts as "just happened" in the steady mode. On a half-year run an
 * exposure lasts a few milliseconds on screen; the narration and the glow on a new tile need this long to
 * register at all.
 */
const RECENT_MS = 600
/**
 * Real time each unit of weight is worth, and the bounds a full loop is kept inside. One exposure gets
 * about two seconds at 1x: the telescope swings over, its fibres land, the targets light and the score
 * ticks, and the picture then holds still long enough to read the caption before the next swing. At a
 * second a beat the caption changed faster than it could be read and the beam never stopped moving.
 * The speed buttons cover viewers who want the whole week faster.
 */
const MS_PER_UNIT = 1900
const LOOP_MIN_MS = 45_000
const LOOP_MAX_MS = 720_000
/** How much sim time a collapsed gap actually shows: the quiet stretch just before the next exposure. */
const GAP_SHOWN_SLOTS = 3
/** One turn of the sky, used to keep a collapsed gap from sweeping the map round more than once. */
const SIDEREAL_DAY = 86164.0905

export const replayMeta = reactive({ version: 0, source: 'demo' as 'demo' | 'champion', label: '' })

export let replaySite: SkySite = { lat: 0, lon: 0, min_alt: 30 }
export let replayTargets: SkyTarget[] = []
export let replaySlots: ReplaySlot[] = []
export let replayActions: ReplayAction[] = []
export let replayTotals = { finalScore: 0, baseScience: 0, completed: 0, nights: 0, requiredMissing: 0 }
export let replayNights: string[] = []
/** Only the exposures, with their index in replayActions — the console draws marks from these. */
export let replayObserves: { i: number; a: ReplayAction }[] = []
/** Running net score: replayNetPrefix[k] covers actions 0…k-1, so the console never scans the run. */
export let replayNetPrefix: number[] = [0]
export let LOOP_MS = 75_000
/** Whether this run draws a "now" cursor at all. A run-level fact, so the legend does not flicker with each fade. */
export let replayHasCursor = true
/** Replay seconds a freshly finished tile keeps glowing, stretched on long runs so the glow is visible at all. */
export let replayPulseSec = SLOT_SECONDS * 2
/** Replay seconds that pass in RECENT_MS of real time while the steady mode is running. */
let recentSec = 0

type Segment = {
  kind: 'observe' | 'gap'
  actionIndex: number
  fromSec: number
  toSec: number
  nights: number
  slots: number
  /** Where the sky starts from, so a gap sweeps across the skipped hours instead of cutting to its end. */
  sweepFromSec: number
  /** This segment's share of the loop. */
  weight: number
  /** Set on a gap too wide to travel: hold, fade out, cut, fade back in. */
  dissolve?: boolean
  /** Set on a gap that skips a daytime; the 3D sky time-lapses across it. */
  lapse?: boolean
}
let segments: Segment[] = []
/** True while the replay is walking one steady beat rather than one beat per exposure. */
let steady = false
/** False when the sky turns too fast for a "now" cursor to mean anything; it is left undrawn then. */
let steadyCursor = true
let segCum: number[] = [0]
let totalWeight = 1

const nightOf = (slotId: string) => slotId.split('-')[0] ?? ''

/**
 * Settle where a gap's sweep starts and what it costs. Whole turns are trimmed off first, so a gap of
 * several nights still crosses the map once; what is left decides how long the gap holds, which keeps
 * every sweep at roughly the same speed however many hours it stands for.
 */
function priceGap(seg: Segment, jumpFromSec: number): Segment {
  let from = jumpFromSec
  const span = seg.toSec - from
  if (span > SIDEREAL_DAY) from = seg.toSec - (span % SIDEREAL_DAY)
  const turn = Math.min(1, Math.max(0, (seg.toSec - from) / SIDEREAL_DAY))
  seg.sweepFromSec = from
  seg.dissolve = turn * 360 > SWEEP_MAX_DEG
  // A daytime, not a long cloudy spell: the gap is long and the run's weather puts its two ends in
  // different nights (a run logged without weather falls back to the length alone).
  const nightAt = (sec: number) => replaySlots[slotIndexAt(sec)]?.night
  seg.lapse = seg.toSec - jumpFromSec >= DAY_GAP_HOURS * 3600 && (!replaySlots.length || nightAt(jumpFromSec) !== nightAt(seg.toSec))
  seg.weight = seg.lapse ? DAY_LAPSE_UNIT : seg.dissolve ? GAP_DISSOLVE_UNIT : GAP_UNIT + turn * GAP_SWEEP_UNIT
  return seg
}

/**
 * A stretch of run time with no actions logged in it at all. Some runs stop writing rows while they
 * wait, so two exposures can sit hours apart with nothing between them; without a segment of its own
 * that stretch would cut the sky straight from one hour angle to another.
 */
function holeSegment(fromSec: number, toSec: number, actionIndex: number): Segment {
  const a = slotIndexAt(fromSec), b = slotIndexAt(toSec)
  const nights = new Set<string>()
  for (let k = Math.min(a, b); k <= Math.max(a, b); k++) {
    const slot = replaySlots[k]
    if (slot) nights.add(slot.night)
  }
  const shown = Math.min(toSec - fromSec, GAP_SHOWN_SLOTS * SLOT_SECONDS)
  return priceGap(
    { kind: 'gap', actionIndex, fromSec: toSec - shown, toSec, nights: nights.size, slots: Math.abs(b - a), sweepFromSec: fromSec, weight: GAP_UNIT },
    fromSec,
  )
}

/** Index of the last action that had started by this replay time. */
export function actionIndexAt(nowSec: number): number {
  let lo = 0, hi = replayActions.length - 1
  if (hi < 0) return 0
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (replayActions[mid]!.startSec <= nowSec) lo = mid; else hi = mid - 1
  }
  return lo
}
/** The latest exposure that had started by this replay time, if any. */
function lastObserveAt(nowSec: number): { i: number; a: ReplayAction } | null {
  let lo = 0, hi = replayObserves.length - 1, found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (replayObserves[mid]!.a.startSec <= nowSec) { found = mid; lo = mid + 1 } else hi = mid - 1
  }
  return found < 0 ? null : replayObserves[found]!
}
/** How many actions have finished by this replay time — what the score and the filled tiles are built from. */
export function settledCountAt(nowSec: number): number {
  let lo = 0, hi = replayActions.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (replayActions[mid]!.doneSec <= nowSec) lo = mid + 1; else hi = mid
  }
  return lo
}

/**
 * One beat for the whole run, walked at a steady rate. Used when there are too many exposures to give
 * each its own beat; nothing is skipped or cut, so the sky simply turns and the tiles fill in.
 */
function buildSteadySegment(): boolean {
  steady = false
  if (replayObserves.length <= DENSE_EXPOSURES) return false
  steady = true
  const first = replayActions[0]!, last = replayActions[replayActions.length - 1]!
  const span = Math.max(1, last.doneSec - first.startSec)
  segments = [{
    kind: 'observe', actionIndex: 0,
    fromSec: first.startSec, toSec: last.doneSec,
    nights: 0, slots: 0, sweepFromSec: first.startSec, weight: 1,
  }]
  segCum = [0, 1]
  totalWeight = 1
  const turns = span / SIDEREAL_DAY
  LOOP_MS = Math.min(LOOP_MAX_MS, Math.max(LOOP_MIN_MS, Math.round(turns * MS_PER_TURN))) + END_HOLD_MS
  steadyCursor = (turns * 360) / ((LOOP_MS - END_HOLD_MS) / 16.7) <= MAX_CURSOR_DEG_PER_FRAME
  replayHasCursor = steadyCursor
  recentSec = span * RECENT_MS / Math.max(1, LOOP_MS - END_HOLD_MS)
  replayPulseSec = Math.max(SLOT_SECONDS * 2, recentSec * 1.5)
  return true
}

function buildSegments() {
  if (buildSteadySegment()) return
  replayHasCursor = true
  replayPulseSec = SLOT_SECONDS * 2
  segments = []
  let i = 0
  while (i < replayActions.length) {
    const action = replayActions[i]!
    if (action.a === 'observe') {
      const prev = segments[segments.length - 1]
      if (prev && action.startSec - prev.toSec > SLOT_SECONDS) {
        segments.push(holeSegment(prev.toSec, action.startSec, i))
      }
      segments.push({ kind: 'observe', actionIndex: i, fromSec: action.startSec, toSec: action.doneSec, nights: 0, slots: 0, sweepFromSec: action.startSec, weight: OBSERVE_UNIT })
      i += 1
      continue
    }
    const start = i
    const nights = new Set<string>()
    while (i < replayActions.length && replayActions[i]!.a !== 'observe') {
      nights.add(nightOf(replayActions[i]!.slot))
      i += 1
    }
    const last = replayActions[i - 1]!
    const endSec = i < replayActions.length ? replayActions[i]!.startSec : last.doneSec
    const shown = Math.min(Math.max(0, endSec - replayActions[start]!.startSec), GAP_SHOWN_SLOTS * SLOT_SECONDS)
    const prev = segments[segments.length - 1]
    segments.push(priceGap(
      {
        kind: 'gap',
        actionIndex: start,
        fromSec: endSec - shown,
        toSec: endSec,
        nights: nights.size,
        slots: i - start,
        sweepFromSec: endSec - shown,
        weight: GAP_UNIT,
      },
      prev ? prev.toSec : endSec - shown,
    ))
  }
  if (!segments.length) {
    segments.push({ kind: 'gap', actionIndex: 0, fromSec: 0, toSec: 1, nights: 0, slots: 0, sweepFromSec: 0, weight: GAP_UNIT })
  }
  segCum = [0]
  for (const seg of segments) segCum.push(segCum[segCum.length - 1]! + seg.weight)
  totalWeight = Math.max(1e-6, segCum[segCum.length - 1]!)
  LOOP_MS = Math.min(LOOP_MAX_MS, Math.max(LOOP_MIN_MS, Math.round(totalWeight * MS_PER_UNIT))) + END_HOLD_MS
}

export function setReplayData(raw: RawReplay, source: 'demo' | 'champion' = 'demo', label = '') {
  replaySite = raw.site
  replayTargets = raw.targets.map(t => ({ id: t.id, ra: t.ra, dec: t.dec, required: t.required }))
  replaySlots = raw.weather.map(w => ({ ...w, startSec: Date.parse(w.t) / 1000 }))
  replayActions = raw.actions.map(a => {
    const startSec = Date.parse(a.t) / 1000
    const act = a.a === 'wait' ? 'wait' as const : 'observe' as const
    return { ...a, a: act, cls: a.cls as OutcomeClass, startSec, doneSec: startSec + a.dt }
  })
  replayTotals = {
    finalScore: raw.score.total, baseScience: raw.score.base_science,
    completed: raw.completed, nights: raw.nights, requiredMissing: raw.required_missing.length,
  }
  replayNights = [...new Set(replaySlots.map(s => s.night))]
  replayObserves = replayActions.map((a, i) => ({ i, a })).filter(entry => entry.a.a === 'observe')
  replayNetPrefix = [0]
  for (const a of replayActions) replayNetPrefix.push(replayNetPrefix[replayNetPrefix.length - 1]! + a.score - a.penalty)
  buildSegments()
  replayMeta.source = source
  replayMeta.label = label
  replayMeta.version += 1
  // A new run starts from its first night, not from wherever the previous run's loop had got to.
  base = 0
  if (runningSince != null) runningSince = performance.now()
  tick()
}

const state = reactive({ progress: 0, slotIndex: 0, actionIndex: 0, paused: false, reduced: false, speed: 1 })
let base = 0, runningSince: number | null = null, users = 0, timer: number | undefined

function elapsedMs(): number {
  return base + (runningSince == null ? 0 : (performance.now() - runningSince) * state.speed)
}
/** Continuous loop progress in [0, 1). Under reduced motion the clock sits at the final state. */
export function replayProgress(): number {
  if (state.reduced) return 0.999999
  return (elapsedMs() % LOOP_MS) / LOOP_MS
}
/** Slot containing a replay time (binary search over slot start times). */
export function slotIndexAt(nowSec: number): number {
  let lo = 0, hi = replaySlots.length - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (replaySlots[mid]!.startSec <= nowSec) lo = mid; else hi = mid - 1
  }
  return lo
}

export interface ReplayFrame {
  actionIndex: number
  slotIndex: number
  nowSec: number
  /**
   * Time to draw the sky at. Equal to nowSec during an exposure. Across a narrow gap it glides through
   * the skipped stretch; across a wide one it holds, cuts once out of sight, and resumes.
   */
  skySec: number
  /** How solid the time cursor and its reach rings should be drawn: 1 normally, dipping to 0 over a cut. */
  skyFade: number
  frac: number
  /** Set while a run of waiting is being shown, with how much of the run it stands for. */
  gap: { nights: number; slots: number } | null
  /**
   * Continuous sky time for the 3D view: equal to nowSec during an exposure, and eased straight across
   * a gap — daytime included — so the sky, sun and moon move through it as a time-lapse with no cut.
   */
  lapseSec: number
  /** True across a gap that skips a daytime. */
  lapse: boolean
  /** True while the finished run is held at the end of the loop. */
  ended: boolean
}
/** Map loop progress onto the run: exposures get equal dwell, waiting runs collapse into short beats. */
export function replayTimeAt(progress: number): ReplayFrame {
  // The last END_HOLD_MS of the loop shows the finished run, standing still.
  const hold = Math.min(0.3, END_HOLD_MS / Math.max(1, LOOP_MS))
  const played = Math.max(0, Math.min(1, progress / (1 - hold)))
  const atEnd = played >= 1
  const v = Math.max(0, Math.min(totalWeight - 1e-9, played * totalWeight))
  let lo = segments.length - 1
  if (!atEnd) {
    lo = 0
    let hi = segments.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (segCum[mid]! <= v) lo = mid; else hi = mid - 1
    }
  }
  const seg = segments[lo]!
  const span = Math.max(1e-6, segCum[lo + 1]! - segCum[lo]!)
  const frac = atEnd ? 1 : Math.max(0, Math.min(1, (v - segCum[lo]!) / span))
  const nowSec = seg.fromSec + frac * (seg.toSec - seg.fromSec)
  if (steady) {
    // Nothing is skipped here, so the sky reads straight off the clock and can never cut.
    const idx = actionIndexAt(nowSec)
    const recent = lastObserveAt(nowSec)
    const live = recent != null && nowSec <= recent.a.doneSec + recentSec
    return {
      actionIndex: live ? recent.i : idx,
      slotIndex: slotIndexAt(nowSec),
      nowSec,
      skySec: nowSec,
      skyFade: steadyCursor ? 1 : 0,
      frac,
      // Between exposures — daytime included — the run is waiting, whatever row was logged last.
      gap: live ? null : { nights: 1, slots: 1 },
      lapseSec: nowSec,
      lapse: false,
      ended: atEnd,
    }
  }
  // The readout skips to the quiet stretch before the next exposure. A short gap lets the sky glide
  // the rest of the way; a wide one holds still, dissolves, and comes back where the night resumes.
  let skySec = nowSec
  let skyFade = 1
  let lapseSec = nowSec
  if (seg.kind === 'gap') {
    // Eased, so the sky pulls away and settles rather than snapping into and out of the drift.
    const e = frac * frac * (3 - 2 * frac)
    lapseSec = seg.sweepFromSec + e * (seg.toSec - seg.sweepFromSec)
    if (seg.dissolve) {
      skySec = frac < 0.5 ? seg.sweepFromSec : seg.toSec
      skyFade = Math.min(1, Math.abs(frac - 0.5) / DISSOLVE_EDGE)
    } else {
      skySec = lapseSec
    }
  }
  return {
    actionIndex: seg.actionIndex,
    slotIndex: slotIndexAt(nowSec),
    nowSec,
    skySec,
    skyFade,
    frac,
    gap: seg.kind === 'gap' ? { nights: seg.nights, slots: seg.slots } : null,
    lapseSec,
    lapse: seg.kind === 'gap' && !!seg.lapse,
    ended: atEnd,
  }
}
/** Loop progress at which each night's first exposure starts — tick marks for the timeline. */
export function replayNightMarks(): { night: number; progress: number }[] {
  const hold = Math.min(0.3, END_HOLD_MS / Math.max(1, LOOP_MS))
  const marks: { night: number; progress: number }[] = []
  const seen = new Set<string>()
  segments.forEach((seg, k) => {
    if (seg.kind !== 'observe') return
    const night = nightOf(replayActions[seg.actionIndex]?.slot ?? '')
    if (!night || seen.has(night)) return
    seen.add(night)
    // Start the mark at the gap before it, so jumping to a night shows its dusk rather than its first exposure mid-way.
    const from = k > 0 && segments[k - 1]!.kind === 'gap' ? k - 1 : k
    // Numbered like the readout, so a night clouded out entirely leaves a hole rather than shifting the rest.
    marks.push({ night: replayNights.indexOf(night) + 1 || seen.size, progress: (segCum[from]! / totalWeight) * (1 - hold) })
  })
  return marks
}
function tick() {
  const p = replayProgress()
  const at = replayTimeAt(p)
  state.progress = p
  state.slotIndex = at.slotIndex
  state.actionIndex = at.actionIndex
}
/** Jump the shared clock to a loop position (the console's drag bar). */
export function seekReplay(progress: number) {
  const p = Math.max(0, Math.min(0.999999, progress))
  base = p * LOOP_MS
  if (runningSince != null) runningSince = performance.now()
  tick()
}
/** Playback rate for the shared clock; the loop position carries over so nothing jumps. */
function setSpeed(speed: number) {
  if (state.speed === speed) return
  base = elapsedMs()
  if (runningSince != null) runningSince = performance.now()
  state.speed = speed
}
function setPaused(paused: boolean) {
  if (state.paused === paused) return
  state.paused = paused
  if (paused) { base = elapsedMs(); runningSince = null }
  else runningSince = performance.now()
}
function acquire() {
  if (users++ > 0) return
  state.reduced = prefersReducedMotion()
  base = 0
  runningSince = state.reduced || state.paused ? null : performance.now()
  tick()
  timer = window.setInterval(tick, 250)
}
function release() {
  if (--users > 0) return
  if (timer) window.clearInterval(timer)
  timer = undefined
  runningSince = null
}

// The bundled demo run is a ~280 KB JSON payload. AppFooter (every page) and the home hero both pull
// from this module, so loading it eagerly put that weight in front of first paint everywhere. Seed a
// safe empty run synchronously (buildSegments() already has a fallback segment for zero actions), then
// fetch the real data as its own chunk; replayMeta.version bumps when it lands, which is what every
// consumer already re-renders on.
// The fetch-and-transform (parsing ~2,900 targets/actions/weather rows into typed arrays) is pushed
// past requestIdleCallback so it runs after the browser is done with the cold-load critical path —
// first paint, layout, hydration — rather than racing it on every single page load.
buildSegments()
function loadDemoReplay() {
  void import('../content/demo/replay.json').then(({ default: demoReplay }) => {
    setReplayData(demoReplay as unknown as RawReplay, 'demo')
  })
}
if (typeof window !== 'undefined') {
  const w = window as unknown as { requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number }
  if (typeof w.requestIdleCallback === 'function') w.requestIdleCallback(loadDemoReplay, { timeout: 2000 })
  else setTimeout(loadDemoReplay, 300)
} else {
  loadDemoReplay()
}

export function useReplayClock() {
  onMounted(acquire)
  onUnmounted(release)
  return { state: readonly(state), setPaused, setSpeed, seek: seekReplay, replayProgress, replayTimeAt }
}
