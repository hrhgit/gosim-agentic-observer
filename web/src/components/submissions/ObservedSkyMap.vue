<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { readObjectText } from '../../lib/storage'
import { drawSkyMap, parseTilesCsv, prefersReducedMotion, type ObservedMark, type SkySite, type SkyTile } from '../../lib/skymap'
import { actionNet, nightOf, outcomeClass, type ReportAction } from '../../lib/report'
import { replaySite } from '../../composables/useReplayClock'
import { fmtUtc, num } from '../../lib/format'

/** `cursor` is the position shared with the decision replay above (actions shown so far); the map follows it and reports its own moves. */
const props = defineProps<{ slug: string; actions: ReportAction[]; tilesPublic: boolean; cursor?: number | null }>()
const emit = defineEmits<{ 'update:cursor': [cursor: number] }>()
const { t } = useI18n()
const canvas = ref<HTMLCanvasElement | null>(null)
const tiles = ref<SkyTile[]>([])
const site = ref<SkySite>(replaySite)
const failed = ref(false)
const loading = ref(true)
const index = ref(0)
const playing = ref(false)
let timer: number | undefined, observer: ResizeObserver | undefined

const total = computed(() => props.actions.length)
const cursor = computed(() => (index.value > 0 ? props.actions[index.value - 1] : null) ?? null)
const nights = computed(() => {
  const map = new Map<string, { night: string; tiles: number; score: number }>()
  for (const a of props.actions) {
    const night = nightOf(a.slot_id)
    const row = map.get(night) ?? { night, tiles: 0, score: 0 }
    if (a.outcome === 'completed') row.tiles++
    row.score += actionNet(a)
    map.set(night, row)
  }
  return [...map.values()]
})

function render() {
  if (!canvas.value || !tiles.value.length) return
  const observed = new Map<string, ObservedMark>()
  let nowSec: number | null = null
  for (const a of props.actions.slice(0, index.value)) {
    const start = a.start_utc ? Date.parse(a.start_utc) / 1000 : NaN
    const done = Number.isFinite(start) ? start + Number(a.elapsed_seconds || 0) : NaN
    if (Number.isFinite(done)) nowSec = done
    if (a.action !== 'observe' || !a.tile_id) continue
    const id = String(a.tile_id)
    const cls = outcomeClass(a.outcome, a.action)
    const prev = observed.get(id)
    if (!prev || cls === 'completed') observed.set(id, { state: cls, doneSec: Number.isFinite(done) ? done : 0 })
  }
  if (nowSec == null && props.actions[0]?.start_utc) nowSec = Date.parse(props.actions[0].start_utc) / 1000
  drawSkyMap(canvas.value, tiles.value, site.value, { nowSec, observed, pulseSeconds: 0 })
}
function stop() { playing.value = false; if (timer) { window.clearInterval(timer); timer = undefined } }
function toggle() {
  if (playing.value) { stop(); return }
  if (index.value >= total.value) index.value = 0
  playing.value = true
  // long runs (hundreds of waits) advance several actions per tick so a full replay stays under a minute
  const step = Math.max(1, Math.ceil(total.value / 160))
  const advance = () => { if (index.value >= total.value) stop(); else index.value = Math.min(total.value, index.value + step) }
  // Step at once: moving the shared cursor is what pauses the replay above, so the two never play together.
  advance()
  if (playing.value) timer = window.setInterval(advance, prefersReducedMotion() ? 700 : 250)
}

onMounted(async () => {
  if (!props.tilesPublic) { loading.value = false; return }
  try {
    const [csv, cfg] = await Promise.all([
      readObjectText('scenarios', `${props.slug}/outputs/reference/tiles.csv`),
      readObjectText('scenarios', `${props.slug}/config/calendar_config.json`).catch(() => null),
    ])
    tiles.value = parseTilesCsv(csv)
    if (!tiles.value.length) failed.value = true
    if (cfg) {
      try {
        const s = JSON.parse(cfg)?.site
        if (s && Number.isFinite(s.latitude_deg)) site.value = { lat: s.latitude_deg, lon: s.longitude_deg, min_alt: 30 }
      } catch { /* keep the default site */ }
    }
  } catch { failed.value = true }
  finally { loading.value = false }
  index.value = props.cursor ?? total.value
  await new Promise(r => requestAnimationFrame(r))
  if (canvas.value) { observer = new ResizeObserver(render); observer.observe(canvas.value) }
  render()
})
onUnmounted(() => { stop(); observer?.disconnect() })
watch(index, i => { render(); if (i !== props.cursor) emit('update:cursor', i) })
// The replay moved: follow it, and stop this map's own playback so only one player drives the position.
watch(() => props.cursor, c => { if (c != null && c !== index.value) { stop(); index.value = Math.max(0, Math.min(total.value, c)) } })
</script>

<template>
  <div class="observed-map" data-testid="observed-sky">
    <h3 class="label">{{ t('subs.skymap.title') }}</h3>
    <p v-if="!tilesPublic" class="text3 mt-2 font-mono text-xs uppercase tracking-[.08em]">{{ t('subs.skymap.hidden') }}</p>
    <p v-else-if="loading" class="text3 mt-2 text-sm">{{ t('common.loading') }}</p>
    <p v-else-if="failed || !tiles.length" class="text3 mt-2 text-sm">{{ t('subs.skymap.unavailable') }}</p>
    <template v-else>
      <div class="observed-frame mt-3">
        <canvas ref="canvas" class="observed-canvas" role="img" :aria-label="t('subs.skymap.title')"></canvas>
      </div>
      <div class="legend mt-2"><span><i class="diamond"></i>REQUIRED</span><span><i style="border:1px solid #78a6ff"></i>FLEXIBLE</span><span><i style="background:#315efb"></i>{{ t('subs.outcome_class.completed') }}</span><span><i style="border:1px solid #b8860b"></i>{{ t('subs.outcome_class.interrupted') }}</span><span><i style="border:1px solid #ff3b3b"></i>{{ t('subs.outcome_class.unsafe') }}</span><span><i style="border:1px solid #7a2a2a"></i>{{ t('subs.outcome_class.invalid') }}</span></div>
      <div class="observed-controls mt-3">
        <button type="button" class="copy-btn" :aria-label="playing ? t('subs.skymap.pause') : t('subs.skymap.play')" data-testid="observed-play" @click="toggle">{{ playing ? '❚❚ ' + t('subs.skymap.pause') : '▶ ' + t('subs.skymap.play') }}</button>
        <input v-model.number="index" type="range" min="0" :max="total" step="1" class="observed-range" :aria-label="t('subs.skymap.decision')" data-testid="observed-range" @input="stop">
        <span class="observed-cursor m">
          <template v-if="cursor">{{ t('subs.skymap.decision') }} {{ cursor.decision_id }} · {{ cursor.slot_id }} · {{ cursor.start_utc ? fmtUtc(cursor.start_utc, { seconds: true }) : '—' }} UTC · {{ cursor.action }} {{ cursor.tile_id }} · {{ cursor.outcome }}</template>
          <template v-else>{{ t('subs.skymap.start') }}</template>
        </span>
      </div>
      <div class="observed-nights mt-3">
        <div v-for="n in nights" :key="n.night" class="observed-night">
          <span class="text3">{{ n.night }}</span>
          <span>{{ n.tiles }} {{ t('subs.skymap.tiles') }}</span>
          <span :class="n.score >= 0 ? 'text-[#78a6ff]' : 'text-[#ff6b6b]'">{{ n.score >= 0 ? '+' : '' }}{{ num(n.score, 1) }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.observed-frame { border: 1px solid rgba(255,255,255,.2); background: rgba(2,5,12,.7); }
.observed-canvas { display: block; width: 100%; aspect-ratio: 5 / 2; min-height: 180px; }
.observed-controls { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; font-variant-numeric: tabular-nums; }
.observed-range { flex: 1 1 12rem; height: 2px; min-width: 8rem; accent-color: #315efb; background: rgba(255,255,255,.2); appearance: none; }
.observed-range::-webkit-slider-thumb { appearance: none; width: 12px; height: 12px; background: #315efb; border: 0; cursor: pointer; }
.observed-range::-moz-range-thumb { width: 12px; height: 12px; background: #315efb; border: 0; border-radius: 0; cursor: pointer; }
.observed-cursor { font-size: .72rem; color: #bdbdbd; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.observed-nights { display: grid; grid-template-columns: repeat(auto-fill, minmax(15.5rem, 1fr)); gap: .5rem 1.5rem; max-height: 11rem; overflow-y: auto; padding-right: .5rem; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .72rem; font-variant-numeric: tabular-nums; }
.observed-night { display: flex; gap: .75rem; border-left: 2px solid #315efb; padding-left: .6rem; color: #f5f5f5; }
.legend i.diamond { border: 1px solid #f5f5f5; transform: rotate(45deg) scale(.8); }
</style>
