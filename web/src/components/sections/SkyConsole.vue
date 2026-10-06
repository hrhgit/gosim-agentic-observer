<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { replayActions, replayMeta, replayNetPrefix, replayNightMarks, replayNights, replayObserves, replaySite, replaySlots, replayTargets, replayTimeAt, replayTotals, replayHasCursor, replayPulseSec, settledCountAt, LOOP_MS, slotIndexAt, useReplayClock, SLOT_SECONDS } from '../../composables/useReplayClock'
import { drawTargetMap, lstDeg, targetRaBounds, raBoundsFrac, PAD, type LivePointing, type ObservedMark } from '../../lib/skymap'
import { applyMatrix, equatorialVec, horizonMatrix, sunRaDec, supportsWebGL } from '../../lib/sky3d'
import { lightsTargets, shownSettled, storyFor } from '../../lib/replayStory'
import { OUTCOME_COLORS } from '../../lib/report'
import { fmtUtc, num } from '../../lib/format'
import ReplaySky3D from './ReplaySky3D.vue'

const { t, tf } = useI18n()
const clock = useReplayClock()
const root = ref<HTMLDivElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
const stage = ref<HTMLDivElement | null>(null)
const sky3d = shallowRef<InstanceType<typeof ReplaySky3D> | null>(null)
/** The 3D sky needs WebGL and motion; without either the flat map carries the replay. */
const webglOk = ref(true)
const use3D = computed(() => webglOk.value && !clock.state.reduced)
const progressUI = ref(0)
const scrubbing = ref(false)
const lstOpen = ref(false)
const seekValue = computed(() => Math.round(progressUI.value * 1000))
function onSeek(e: Event) {
  const v = (e.target as HTMLInputElement).valueAsNumber / 1000
  progressUI.value = v
  clock.seek(v)
}
const SPEEDS = [1, 3, 10] as const
const hud = ref({ date: '', utc: '', lst: '', seeing: 0, transp: 0, sky: 0, eff: 0, open: true, night: true, sunUp: false, score: 0, completed: 0, nightNo: 1 })
const story = ref(storyFor({ loaded: false, ended: false, kind: 'gap', frac: 0, lapse: false, sunUp: false, open: true, action: null, nightNo: 1, nights: 1, totals: { done: 0, total: 0, score: 0 } }))
const paused = computed(() => clock.state.paused)
const reduced = computed(() => clock.state.reduced)
let raf = 0, observer: ResizeObserver | undefined, visibility: IntersectionObserver | undefined, shownScore = 0, lastProgress = 0
// The flat map redraws thousands of point sources every tick; capping its redraw rate (and pausing
// everything while the console is scrolled off-screen or the tab is hidden) keeps that cost off a
// fanless laptop's main thread. The 3D sky draws every frame so the beam and camera move smoothly,
// but the readout under it only needs refreshing a few times a second.
const RENDER_INTERVAL_MS = 1000 / 24
const HUD_INTERVAL_MS = 100
let lastRenderAt = 0, lastHudAt = 0
let isVisible = true
const PULSE = SLOT_SECONDS * 2  // glow for two slots of replay time after a tile completes
/** Stands in for the weather row when a run arrives without one, so the readout stays up instead of throwing. */
const EMPTY_SLOT = { slot: '—', night: '', t: '', startSec: 0, open: true, seeing: 0, transp: 0, sky: 0, eff: 0 }
const DEG = Math.PI / 180

/** Per-run lookups, rebuilt when a run is loaded. */
const runIndex = computed(() => {
  void replayMeta.version
  const required = new Set(replayTargets.filter(tg => tg.required).map(tg => tg.id))
  const ordinal = new Map<number, number>()
  replayObserves.forEach((entry, k) => ordinal.set(entry.i, k + 1))
  return { required, ordinal, nights: replayNights, marks: replayNightMarks() }
})
const loaded = computed(() => { void replayMeta.version; return replayActions.length > 0 })
const caption = computed(() => tf(`hero.console.story.${story.value.key}`, story.value.params))

/** Weather row in force at a moment, or null in daytime (the run logs weather only for its nights). */
function slotAt(sec: number) {
  if (!replaySlots.length) return null
  const slot = replaySlots[slotIndexAt(sec)]
  if (!slot || sec < slot.startSec || sec >= slot.startSec + SLOT_SECONDS) return null
  return slot
}
const nightNoOf = (night: string) => Math.max(1, runIndex.value.nights.indexOf(night) + 1)

/** Previous pointing the telescope swings from, or null after a break (it starts from parked). */
function previousCenter(actionIndex: number) {
  const prev = replayActions[actionIndex - 1]
  return prev && prev.a === 'observe' && prev.center ? prev.center : null
}

function observedAt(settled: number) {
  const observed = new Map<string, ObservedMark>()
  for (const entry of replayObserves) {
    if (entry.i >= settled) break
    const a = entry.a
    // An exposure paid nothing for (dome shut) observed nothing; the readout leaves it out too.
    if (a.cls === 'completed' && !lightsTargets(a)) continue
    for (const target of a.targets) {
      const prev = observed.get(target)
      if (!prev || a.cls === 'completed') observed.set(target, { state: a.cls, doneSec: a.doneSec })
    }
  }
  return observed
}

/** Where the meridian sits inside the flat map, so its hover label can follow it. */
const meridianLeft = ref('50%')
const meridianOnScreen = ref(true)
function trackMeridian(nowSec: number) {
  const el = canvas.value
  if (!el || !el.clientWidth) return
  const plotW = el.clientWidth - PAD.left - PAD.right
  const frac = raBoundsFrac(targetRaBounds(replayTargets), lstDeg(replaySite.lon, nowSec))
  meridianOnScreen.value = frac != null
  if (frac == null) return
  meridianLeft.value = `${((PAD.left + frac * plotW) / el.clientWidth) * 100}%`
}

function updateReadout(progress: number, fr: ReturnType<typeof replayTimeAt>, settled: number, sunUp: boolean) {
  const score = replayNetPrefix[Math.max(0, Math.min(replayNetPrefix.length - 1, settled))] ?? 0
  if (progress < lastProgress) shownScore = 0  // loop restarted
  lastProgress = progress
  if (!scrubbing.value) progressUI.value = progress
  shownScore = reduced.value ? score : shownScore + (score - shownScore) * 0.35
  if (Math.abs(score - shownScore) < 0.05) shownScore = score
  const sec = fr.lapseSec
  const slot = slotAt(sec)
  const nearest = replaySlots[slotIndexAt(sec)] ?? EMPTY_SLOT
  const stamp = fmtUtc(new Date(sec * 1000).toISOString(), { seconds: true, short: true })
  const lstHours = lstDeg(replaySite.lon, fr.skySec) / 15
  const lst = `${String(Math.floor(lstHours)).padStart(2, '0')}:${String(Math.floor((lstHours % 1) * 60)).padStart(2, '0')}`
  let completed = 0
  const done = new Set<string>()
  for (const entry of replayObserves) {
    if (entry.i >= settled) break
    if (!lightsTargets(entry.a)) continue
    for (const id of entry.a.targets) if (!done.has(id)) { done.add(id); completed += 1 }
  }
  const action = replayActions[fr.actionIndex]
  const live = !fr.gap && action?.a === 'observe'
  // Past the middle of a skipped day the readout already belongs to the night about to start, so dusk
  // reads "night 3 begins", not "night 2".
  const upcoming = fr.lapse && fr.frac >= 0.5 ? replayActions.slice(fr.actionIndex).find(a => a.a === 'observe') : undefined
  const nightNo = upcoming ? nightNoOf(upcoming.slot.split('-')[0] ?? '') : nightNoOf(nearest.night)
  const s = slot ?? EMPTY_SLOT
  hud.value = {
    date: stamp.slice(0, 5), utc: stamp.slice(6, 11), lst,
    seeing: s.seeing, transp: s.transp, sky: s.sky, eff: s.eff, open: s.open, night: !!slot, sunUp,
    score: shownScore, completed, nightNo,
  }
  story.value = storyFor({
    loaded: loaded.value,
    ended: fr.ended,
    kind: live ? 'observe' : 'gap',
    frac: fr.frac,
    lapse: fr.lapse,
    sunUp,
    open: slot ? slot.open : true,
    action: live && action ? {
      hits: action.targets.length,
      required: action.targets.filter(id => runIndex.value.required.has(id)).length,
      score: action.score - action.penalty,
      ordinal: runIndex.value.ordinal.get(fr.actionIndex) ?? 0,
    } : null,
    nightNo,
    nights: replayTotals.nights,
    totals: { done: completed, total: replayTargets.length, score },
  })
}

function render(ts = performance.now()) {
  const progress = clock.replayProgress()
  const fr = replayTimeAt(progress)
  const action = replayActions[fr.actionIndex]
  // Read the beat off the exposure's own times: equal to fr.frac when each exposure has its own beat,
  // and still right when a dense run plays as one long beat.
  const phase = action ? Math.max(0, Math.min(1, (fr.nowSec - action.startSec) / Math.max(1, action.doneSec - action.startSec))) : 0
  const live = !fr.gap && action?.a === 'observe' && action.center ? { index: fr.actionIndex, phase } : null
  // The exposure on screen counts from the moment its fibres land, not from the end of its beat.
  const settled = shownSettled(settledCountAt(fr.nowSec), live)
  fadeLoopEdges(progress)
  if (use3D.value) {
    const sec = fr.lapseSec
    const lst = lstDeg(replaySite.lon, sec)
    const slot = slotAt(sec)
    sky3d.value?.draw({
      t: sec, lst, settled, live,
      from: live ? previousCenter(fr.actionIndex) : null,
      open: slot ? slot.open : true,
      transp: slot ? slot.transp : 1,
      version: replayMeta.version,
    })
    if (ts - lastHudAt < HUD_INTERVAL_MS) return
    lastHudAt = ts
    const sun = sunRaDec(sec)
    const sunUp = applyMatrix(horizonMatrix(replaySite.lat, lst), equatorialVec(sun.ra, sun.dec)).y > Math.sin(-0.8 * DEG)
    updateReadout(progress, fr, settled, sunUp)
    return
  }
  if (ts - lastRenderAt < RENDER_INTERVAL_MS && !reduced.value) return
  lastRenderAt = ts
  const sun = sunRaDec(fr.lapseSec)
  const sunUp = applyMatrix(horizonMatrix(replaySite.lat, lstDeg(replaySite.lon, fr.lapseSec)), equatorialVec(sun.ra, sun.dec)).y > 0
  updateReadout(progress, fr, settled, sunUp)
  trackMeridian(fr.skySec)
  const livePointing: LivePointing | null = action && action.a === 'observe' && action.center ? { ra: action.center.ra, dec: action.center.dec, targets: action.targets } : null
  if (canvas.value) drawTargetMap(canvas.value, replayTargets, replaySite, { nowSec: fr.skySec, observed: observedAt(settled), timeFade: fr.skyFade, pulseSeconds: reduced.value ? 0 : Math.max(PULSE, replayPulseSec), livePointing })
}
/**
 * The loop dips to black for a moment where it wraps, so the finished week does not cut straight to an
 * empty first night (every lit target going dark and the beam jumping in a single frame).
 */
const LOOP_FADE_MS = 700
function fadeLoopEdges(progress: number) {
  // The picture only: the walkthrough card shares the stage and must never be dimmed.
  const el = stage.value?.querySelector<HTMLElement>('.sky3d, .sky-canvas')
  if (!el) return
  const ms = progress * LOOP_MS
  const edge = reduced.value ? 1 : Math.min(1, ms / LOOP_FADE_MS, (LOOP_MS - ms) / LOOP_FADE_MS)
  const value = (0.15 + 0.85 * Math.max(0, edge)).toFixed(2)
  if (el.style.opacity !== value) el.style.opacity = value
}
function loop(ts: number) {
  render(ts)
  if (!reduced.value && isVisible && !document.hidden) raf = requestAnimationFrame(loop)
  else raf = 0
}
function startLoop() { if (!raf && !reduced.value && isVisible && !document.hidden) raf = requestAnimationFrame(loop) }
function onVisibilityChange() {
  if (document.hidden) { cancelAnimationFrame(raf); raf = 0 }
  else startLoop()
}

// --- "what am I looking at" walkthrough --------------------------------------------------------
// Opened only from the link; never on its own. The replay holds still while it is open.
const TOUR_STEPS_3D = ['dome', 'beam', 'limits', 'score'] as const
const TOUR_STEPS_2D = ['axes', 'tiles', 'meridian', 'hud'] as const
const cursorShown = computed(() => { void replayMeta.version; return replayHasCursor })
const tourSteps = computed<readonly string[]>(() => use3D.value ? TOUR_STEPS_3D : TOUR_STEPS_2D.filter(step => step !== 'meridian' || cursorShown.value))
const tourStep = ref(-1)
const tourOpen = computed(() => tourStep.value >= 0)
const currentStep = computed(() => tourSteps.value[tourStep.value] ?? null)
let pausedBeforeTour = false
function startTour() { if (tourOpen.value) return; pausedBeforeTour = paused.value; tourStep.value = 0; clock.setPaused(true) }
function nextStep() {
  if (tourStep.value < tourSteps.value.length - 1) { tourStep.value += 1; return }
  endTour()
}
function endTour() {
  tourStep.value = -1
  clock.setPaused(pausedBeforeTour)
}
watch(() => tourSteps.value.length, n => { if (tourStep.value >= n) tourStep.value = n - 1 })

onMounted(() => {
  webglOk.value = supportsWebGL()
  if (canvas.value) { observer = new ResizeObserver(() => { if (!use3D.value) render() }); observer.observe(canvas.value) }
  const host = root.value
  if (host) {
    visibility = new IntersectionObserver(entries => {
      isVisible = entries.some(entry => entry.isIntersecting)
      if (isVisible) startLoop()
    }, { threshold: 0.05 })
    visibility.observe(host)
  }
  document.addEventListener('visibilitychange', onVisibilityChange)
  render()
  startLoop()
})
// The frame loop does not run under reduced motion, so a newly loaded run has to be drawn on arrival.
watch(() => replayMeta.version, () => render(), { flush: 'post' })
// The flat map's canvas only exists once the 3D view has bowed out.
watch(canvas, el => {
  if (!el) return
  observer?.disconnect()
  observer = new ResizeObserver(() => render())
  observer.observe(el)
  render()
}, { flush: 'post' })

onUnmounted(() => {
  cancelAnimationFrame(raf)
  observer?.disconnect()
  visibility?.disconnect()
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <div ref="root" class="sky-console" :class="{ 'is-3d': use3D }" data-testid="sky-console" :data-replay-source="replayMeta.source">
    <div class="sky-console-head">
      <span class="sky-live-title flex items-center gap-3"><span class="live-dot" :class="{ 'is-paused': paused || reduced }"></span><span>{{ t('hero.console.title') }}</span></span>
      <span class="text-white/60">{{ paused ? t('hero.console.paused') : tf('hero.console.replay_note', { nights: replayTotals.nights, actions: replayActions.length }) }}</span>
    </div>
    <p class="sky-explainer">
      {{ t(use3D ? 'hero.console.explainer_3d' : 'hero.console.explainer') }}
      <button type="button" class="sky-tour-link" data-testid="sky-tour-open" @click="startTour">{{ t('hero.console.tour_replay') }}</button>
    </p>
    <div ref="stage" class="sky-stage">
      <ReplaySky3D v-if="use3D" ref="sky3d" :highlight="currentStep === 'beam' ? 'fibres' : null" role="img" :aria-label="t('hero.console.aria_3d')" @unavailable="webglOk = false" />
      <canvas v-else ref="canvas" class="sky-canvas" role="img" :aria-label="t('hero.console.aria')"></canvas>
      <!-- Hover label for the flat map's cursor: only for a mouse — a tap sends no "leave" and pinned it open. -->
      <div v-if="!use3D && cursorShown && meridianOnScreen" class="sky-meridian-hit" :style="{ left: meridianLeft }" aria-hidden="true" @pointerenter="e => { if (e.pointerType === 'mouse') lstOpen = true }" @pointerleave="lstOpen = false"></div>
      <div v-if="!use3D && lstOpen && cursorShown && meridianOnScreen" class="sky-lst" :style="{ left: meridianLeft }">{{ tf('hero.console.lst', { lst: hud.lst }) }}</div>
      <div v-if="tourOpen" class="sky-tour" role="dialog" aria-modal="false" :aria-label="t('hero.console.tour_title')">
        <div class="sky-tour-card" :class="[`at-${currentStep}`, { 'is-3d': use3D }]">
          <button type="button" class="sky-tour-close" :aria-label="t('hero.console.tour_close')" data-testid="sky-tour-close" @click="endTour">×</button>
          <p class="sky-tour-step">{{ tourStep + 1 }} / {{ tourSteps.length }}</p>
          <p class="sky-tour-text">{{ t(`hero.console.${use3D ? 'tour3d' : 'tour'}.${currentStep}`) }}</p>
          <p class="sky-tour-actions">
            <button type="button" class="replay-toggle" @click="nextStep">
              {{ tourStep === tourSteps.length - 1 ? t('hero.console.tour_done') : t('hero.console.tour_next') }}
            </button>
            <button type="button" class="sky-tour-link" @click="endTour">{{ t('hero.console.tour_skip') }}</button>
          </p>
        </div>
      </div>
    </div>
    <p class="sky-narration" :class="`is-${story.key}`" aria-live="polite" data-testid="sky-narration">
      <span class="sky-narration-dot"></span>
      <span>{{ caption }}</span>
    </p>
    <div class="sky-transport">
      <button type="button" class="replay-toggle sky-play" :aria-pressed="paused" :disabled="reduced" :aria-label="paused ? t('hero.console.resume') : t('hero.console.pause')" @click="clock.setPaused(!paused)">
        <svg v-if="paused || reduced" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 1.5v9l7.5-4.5z" fill="currentColor" /></svg>
        <svg v-else viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 1.5h2.5v9H2.5zM7 1.5h2.5v9H7z" fill="currentColor" /></svg>
      </button>
      <div class="sky-seek">
        <input
          type="range" min="0" max="1000" step="1"
          :value="seekValue" :disabled="reduced" :aria-label="t('hero.console.seek')"
          @pointerdown="scrubbing = true" @pointerup="scrubbing = false" @pointercancel="scrubbing = false"
          @input="onSeek"
        >
        <div class="sky-ticks" aria-hidden="true">
          <span v-for="(m, k) in runIndex.marks" :key="m.night" :class="{ 'is-tight': (runIndex.marks[k + 1]?.progress ?? 2) - m.progress < 0.06 }" :style="{ left: `${m.progress * 100}%` }">{{ tf('hero.console.night_tick', { n: m.night }) }}</span>
        </div>
      </div>
      <div class="sky-speed" role="group" :aria-label="t('hero.console.speed')">
        <button v-for="s in SPEEDS" :key="s" type="button" :aria-pressed="clock.state.speed === s" :disabled="reduced" @click="clock.setSpeed(s)">{{ s }}×</button>
      </div>
    </div>
    <div v-if="use3D" class="sky-legend" aria-hidden="true">
      <span><i class="dot" style="background:#ff9933;box-shadow:0 0 6px #ff9933"></i>{{ t('hero.console.legend_required') }}</span>
      <span><i class="dot" style="background:#5c80db;box-shadow:0 0 6px #5c80db"></i>{{ t('hero.console.legend_flexible') }}</span>
      <span><i class="dot is-dim"></i>{{ t('hero.console.legend_waiting') }}</span>
      <span><i class="dot is-bright"></i>{{ t('hero.console.legend_done') }}</span>
      <span><i class="beam"></i>{{ t('hero.console.legend_beam') }}</span>
      <span><i class="dash"></i>{{ t('hero.console.legend_min_alt') }}</span>
    </div>
    <div v-else class="sky-legend" aria-hidden="true">
      <span><i class="diamond"></i>{{ t('hero.console.legend_required') }}</span>
      <span><i style="border-color:#78a6ff"></i>{{ t('hero.console.legend_flexible') }}</span>
      <span><i :style="{ background: OUTCOME_COLORS.completed, borderColor: OUTCOME_COLORS.completed }"></i>{{ t('hero.console.legend_completed') }}</span>
      <span><i :style="{ borderColor: OUTCOME_COLORS.interrupted }"></i>{{ t('hero.console.legend_interrupted') }}</span>
      <span v-if="cursorShown"><i class="meridian"></i>{{ t('hero.console.legend_meridian') }}</span>
    </div>
    <dl class="sky-hud" aria-live="off">
      <div class="sky-hud-time"><dt>{{ tf('hero.console.hud_time', { n: hud.nightNo, nights: replayTotals.nights }) }}</dt><dd data-testid="sky-time">{{ hud.date }} · {{ hud.utc }} <small>UTC</small></dd></div>
      <div class="sky-hud-weather" :title="t('hero.console.weather_help')">
        <dt>{{ t('hero.console.weather') }}</dt>
        <dd v-if="!hud.night" :class="hud.sunUp ? 'text-[#ffd27a]' : 'text-white/60'">{{ t(hud.sunUp ? 'hero.console.daytime' : 'hero.console.off_hours') }}</dd>
        <dd v-else-if="hud.open">{{ num(hud.seeing, 2) }}″ · {{ num(hud.transp, 2) }} · {{ num(hud.sky, 2) }} · {{ num(hud.eff, 2) }}</dd>
        <dd v-else class="text-[#ff6b6b]">{{ t('hero.console.dome_closed') }}</dd>
      </div>
      <div><dt>{{ t('hero.console.score') }}</dt><dd class="sky-hud-score">{{ num(hud.score, 1) }}</dd></div>
      <div class="sky-hud-targets">
        <dt>{{ t('hero.console.tiles') }}</dt>
        <dd>{{ hud.completed }} / {{ replayTargets.length }}</dd>
        <span class="sky-hud-bar" aria-hidden="true"><i :style="{ width: `${replayTargets.length ? (hud.completed / replayTargets.length) * 100 : 0}%` }"></i></span>
      </div>
    </dl>
  </div>
</template>

<style scoped>
.sky-console {
  position: relative;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(255,255,255,.28);
  /* Sides fall away towards the bottom instead of ruling a flat rectangle. */
  border-image: linear-gradient(180deg, rgba(255,255,255,.4), rgba(255,255,255,.28) 45%, rgba(255,255,255,.1)) 1;
  background: rgba(2,5,12,.72);
}
.sky-console::before {
  position: absolute; top: -1px; left: 0; width: 3.5rem; height: 2px; content: ''; background: #315efb;
}
.sky-console-head {
  display: flex; justify-content: space-between; align-items: center; gap: .4rem 1rem; flex-wrap: wrap;
  padding: .7rem .9rem;
  border-bottom: 1px solid rgba(255,255,255,.16);
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: .68rem; letter-spacing: .12em; text-transform: uppercase; color: #a8a8a8;
}
.live-dot.is-paused { animation: none; opacity: .5; }
.replay-toggle {
  border: 1px solid rgba(255,255,255,.28);
  padding: 2px 10px;
  font: inherit;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: rgba(255,255,255,.75);
  background: transparent;
  cursor: pointer;
}
.replay-toggle:hover:not(:disabled) { border-color: #315efb; color: #78a6ff; }
.replay-toggle:disabled { opacity: .4; cursor: default; }
.sky-explainer {
  margin: 0;
  padding: .6rem .9rem;
  border-bottom: 1px solid rgba(255,255,255,.1);
  font-size: .78rem; line-height: 1.6; color: rgba(255,255,255,.66);
}
.sky-tour-link {
  border: 0; padding: 0; margin-left: .35rem;
  font: inherit; color: #78a6ff; background: none; cursor: pointer; text-decoration: underline;
}
.sky-stage { position: relative; }
.sky-canvas { display: block; width: 100%; aspect-ratio: 3 / 2; min-height: 200px; }
.is-3d .sky-stage { aspect-ratio: 4 / 3; min-height: 280px; background: radial-gradient(ellipse at 50% 40%, rgba(30,48,100,.35), transparent 70%); }
@media (max-width: 560px) { .is-3d .sky-stage { aspect-ratio: 1 / 1.05; } }

.sky-tour { position: absolute; inset: 0; z-index: 8; background: rgba(2,5,12,.22); }
.sky-tour-card {
  position: absolute; left: 50%; transform: translateX(-50%);
  width: min(30rem, calc(100% - 2rem));
  padding: .85rem 1rem;
  border: 1px solid rgba(120,166,255,.55);
  background: rgba(4,8,18,.94);
}
.sky-tour-card.at-axes, .sky-tour-card.at-hud { top: 12%; }
.sky-tour-card.at-tiles, .sky-tour-card.at-meridian { bottom: 8%; }
/* In 3D the card sits low on the left, over the ground, so the sky, the beam and the inset it talks about stay in view. */
.sky-tour-card.is-3d { left: .6rem; bottom: .6rem; top: auto; transform: none; width: min(20rem, calc(100% - 1.2rem)); padding: .7rem .85rem; }
.sky-tour-card.is-3d .sky-tour-text { font-size: .76rem; line-height: 1.6; }
@media (max-width: 560px) { .sky-tour-card.is-3d { width: calc(100% - 1.2rem); } }
.sky-tour-step {
  margin: 0 0 .35rem;
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: .6rem; letter-spacing: .12em; color: #78a6ff;
}
.sky-tour-text { margin: 0; padding-right: 1.4rem; font-size: .8rem; line-height: 1.65; color: #f5f5f5; }
.sky-tour-close {
  position: absolute; top: .35rem; right: .5rem;
  width: 1.6rem; height: 1.6rem; padding: 0;
  border: 1px solid rgba(255,255,255,.35); border-radius: 50%;
  background: rgba(255,255,255,.08); color: #f5f5f5;
  font-size: 1rem; line-height: 1; cursor: pointer;
}
.sky-tour-close:hover { border-color: #78a6ff; color: #78a6ff; }
.sky-tour-actions { display: flex; align-items: center; gap: .9rem; margin: .7rem 0 0; }

.sky-narration {
  display: flex; align-items: center; gap: .55rem;
  margin: 0; padding: .6rem .9rem;
  border-top: 1px solid rgba(255,255,255,.12);
  font-size: .86rem; line-height: 1.45; color: #f2f5ff;
  min-height: 3rem;
  background: linear-gradient(90deg, rgba(49,94,251,.16), transparent 70%);
}
.sky-narration-dot { width: .5rem; height: .5rem; flex: none; border-radius: 50%; background: #78a6ff; box-shadow: 0 0 8px #78a6ff; }
.sky-narration.is-observe_required .sky-narration-dot { background: #ffb648; box-shadow: 0 0 8px #ffb648; }
.sky-narration.is-day, .sky-narration.is-dawn, .sky-narration.is-dusk { background: linear-gradient(90deg, rgba(255,190,90,.14), transparent 70%); }
.sky-narration.is-day .sky-narration-dot, .sky-narration.is-dawn .sky-narration-dot, .sky-narration.is-dusk .sky-narration-dot { background: #ffd27a; box-shadow: 0 0 8px #ffd27a; }
.sky-narration.is-closed, .sky-narration.is-observe_blocked { background: linear-gradient(90deg, rgba(255,107,107,.13), transparent 70%); }
.sky-narration.is-closed .sky-narration-dot, .sky-narration.is-observe_blocked .sky-narration-dot { background: #ff6b6b; box-shadow: 0 0 8px #ff6b6b; }
.sky-narration.is-wait .sky-narration-dot, .sky-narration.is-loading .sky-narration-dot { background: rgba(255,255,255,.4); box-shadow: none; }
.sky-narration.is-done .sky-narration-dot { background: #7be3a6; box-shadow: 0 0 8px #7be3a6; }

.sky-transport { display: flex; align-items: center; gap: .7rem; padding: .55rem .9rem .35rem; border-top: 1px solid rgba(255,255,255,.08); }
.sky-play { display: grid; place-items: center; width: 1.9rem; height: 1.9rem; padding: 0; flex: none; }
.sky-play svg { width: .75rem; height: .75rem; }
.sky-seek { position: relative; flex: 1; min-width: 0; padding-bottom: .95rem; }
.sky-seek input { display: block; width: 100%; height: 4px; margin: .45rem 0 0; accent-color: #315efb; cursor: pointer; }
.sky-seek input:disabled { opacity: .35; cursor: default; }
.sky-ticks { position: absolute; left: 0; right: 0; bottom: 0; height: .9rem; pointer-events: none; }
.sky-ticks span {
  position: absolute; top: 0; padding-left: 3px; border-left: 1px solid rgba(255,255,255,.25);
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .52rem; letter-spacing: .04em; line-height: .9rem; color: rgba(255,255,255,.42); white-space: nowrap;
}
@media (max-width: 560px) { .sky-ticks span.is-tight { color: transparent; } }
.sky-speed { display: flex; flex: none; border: 1px solid rgba(255,255,255,.22); }
.sky-speed button {
  padding: 3px 7px; border: 0; background: transparent; cursor: pointer;
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .64rem; color: rgba(255,255,255,.6);
}
.sky-speed button + button { border-left: 1px solid rgba(255,255,255,.15); }
.sky-speed button[aria-pressed='true'] { background: rgba(49,94,251,.35); color: #fff; }
.sky-speed button:disabled { opacity: .4; cursor: default; }

.sky-legend {
  display: flex; flex-wrap: wrap; gap: .35rem 1rem;
  padding: .45rem .9rem .5rem;
  border-top: 1px solid rgba(255,255,255,.1);
  font-size: .68rem; color: rgba(255,255,255,.58);
}
.sky-legend i { display: inline-block; width: .55rem; height: .55rem; margin-right: .4rem; border: 1px solid; vertical-align: middle; }
.sky-legend i.dot { border: 0; border-radius: 50%; }
.sky-legend i.dot.is-dim { background: rgba(140,165,230,.35); }
.sky-legend i.dot.is-bright { background: #eaf3ff; box-shadow: 0 0 7px #cfe6ff; }
.sky-legend i.beam { width: .9rem; height: .5rem; border: 0; clip-path: polygon(0 40%, 100% 0, 100% 100%, 0 60%); background: linear-gradient(90deg, rgba(127,168,255,.3), #9ec0ff); }
.sky-legend i.dash { width: .9rem; height: 0; border-width: 1px 0 0; border-style: dashed; border-color: #ffcf8a; }
.sky-legend i.diamond { border-color: #f5f5f5; transform: rotate(45deg) scale(.85); }
.sky-legend i.meridian { width: 0; height: .7rem; border-width: 0 0 0 1px; border-style: dashed; border-color: #315efb; }
.sky-hud {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0;
  margin: 0; border-top: 1px solid rgba(255,255,255,.16);
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums;
}
.sky-hud > div { position: relative; min-width: 0; padding: .6rem .7rem; border-right: 1px solid rgba(255,255,255,.1); border-bottom: 1px solid rgba(255,255,255,.1); }
.sky-hud dt { font-size: .58rem; letter-spacing: .07em; text-transform: uppercase; color: rgba(255,255,255,.45); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sky-hud dd { margin: .15rem 0 0; font-size: .74rem; color: #f5f5f5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sky-hud dd small { font-size: .56rem; color: rgba(255,255,255,.45); }
.sky-hud-score { color: #ffe08a !important; font-size: .9rem !important; font-weight: 700; }
.sky-hud-time, .sky-hud-weather { grid-column: span 2; }
.sky-hud-bar { position: absolute; left: .7rem; right: .7rem; bottom: .35rem; height: 2px; background: rgba(255,255,255,.1); }
.sky-hud-bar i { display: block; height: 100%; background: linear-gradient(90deg, #5c80db, #cfe6ff); }
@media (min-width: 640px) {
  .sky-hud { grid-template-columns: auto minmax(0, 1fr) auto auto; }
  .sky-hud-time, .sky-hud-weather { grid-column: auto; }
  .sky-hud > div { border-bottom: 0; }
  .sky-hud > div:last-child { border-right: 0; }
}
.sky-console-head .sky-live-title { font-size: 1.04rem; font-weight: 650; letter-spacing: .01em; color: #fff; text-transform: none; }
.sky-meridian-hit { position: absolute; top: 0; bottom: 0; width: 14px; transform: translateX(-50%); cursor: help; z-index: 3; }
.sky-lst { position: absolute; top: 10px; transform: translateX(-50%); z-index: 6; white-space: nowrap; padding: .32rem .6rem; border: 1px solid rgba(148,163,255,.45); background: rgba(5,9,20,.94); font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .62rem; letter-spacing: .05em; color: #cfe0ff; pointer-events: none; }
</style>
