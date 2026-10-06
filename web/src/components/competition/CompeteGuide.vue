<script setup lang="ts">
// The 参赛 workspace guide: a spotlight tour over the simplified layout (steps and copy in lib/competeGuide).
// Renders the 「新手指南」 button (replay any time) and the tour itself. The first visit opens it automatically as a
// site popup (stores/overlay: one popup per page load; stores/popupSeen: seen once per person, locally and on the
// server). Steps whose target is not on the page are skipped; tabs are switched as needed and restored afterwards.
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { COMPETE_GUIDE_KEY, COMPETE_GUIDE_POPUP, GUIDE_STEPS, GUIDE_UI, clipRect, guideLang, placeTooltip, resolveStep,
  shouldAutoShow, stepCopy, type GuideTab, type Rect, type ResolvedStep } from '../../lib/competeGuide'
import { overlayActive, releaseOverlay, requestOverlay } from '../../stores/overlay'
import { seenKeys, useSeenWhileOpen } from '../../stores/popupSeen'

const { locale } = useI18n()
const ui = computed(() => GUIDE_UI[guideLang(locale.value)])
const available = ref(false)
const open = ref(false)
const steps = ref<ResolvedStep[]>([])
const index = ref(0)
const hole = ref<Rect | null>(null)
const tipPos = ref<{ top: number; left: number } | null>(null)
const tip = ref<HTMLElement | null>(null)
const nextBtn = ref<HTMLButtonElement | null>(null)
let returnFocus: HTMLElement | null = null
let startTab: GuideTab | null = null
const autoRequested = ref(false)
let disposed = false

const current = computed(() => steps.value[index.value] ?? null)
const copy = computed(() => current.value ? stepCopy(current.value, locale.value) : null)
const last = computed(() => index.value >= steps.value.length - 1)
const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
const q = (selector: string) => document.querySelector<HTMLElement>(selector)

useSeenWhileOpen(open, () => [COMPETE_GUIDE_KEY])

function activeTab(): GuideTab | null {
  const on = q('[data-testid^="compete-tab-"][aria-selected="true"]')
  const id = on?.dataset.testid?.slice('compete-tab-'.length)
  return id === 'progress' || id === 'history' || id === 'settings' ? id : null
}
async function showTab(tab: GuideTab | null) {
  if (!tab || activeTab() === tab) return
  q(`[data-testid="compete-tab-${tab}"]`)?.click()
  await nextTick()
}

async function start() {
  if (open.value || !q('[data-testid="compete-tabs"]')) return
  returnFocus = document.activeElement as HTMLElement | null
  startTab = activeTab()
  // Resolve every step up front (switching tabs to look) so the counter is exact.
  const found: ResolvedStep[] = []
  for (const step of GUIDE_STEPS) {
    await showTab(step.tab)
    const r = resolveStep(step, s => !!q(s))
    if (r) found.push(r)
  }
  if (disposed || !found.length) { await showTab(startTab); finishOverlay(); return }
  steps.value = found
  index.value = 0
  open.value = true
  document.addEventListener('keydown', onKey, true)
  window.addEventListener('scroll', schedule, { passive: true })
  window.addEventListener('resize', schedule)
  await go(0)
}

async function go(i: number) {
  const r = steps.value[i]
  if (!r) return
  index.value = i
  await showTab(r.step.tab)
  const el = q(r.selector)
  if (!el) return
  // Bring the target just under the sticky header; the tooltip goes below or above it (lib/competeGuide).
  const header = q('header')?.getBoundingClientRect().height ?? 0
  const rect = el.getBoundingClientRect()
  const room = window.innerHeight - header - 24
  const top = rect.height + 180 <= room ? window.scrollY + rect.top - header - Math.max(16, (room - rect.height - 180) / 3)
    : window.scrollY + rect.top - header - 16
  window.scrollTo({ top: Math.max(0, top), behavior: reduced() ? 'auto' : 'smooth' })
  await nextTick()
  measure()
  nextBtn.value?.focus({ preventScroll: true })
  // Smooth scrolling settles after the first measure; follow it.
  if (!reduced()) setTimeout(measure, 450)
}

let frame = 0
function schedule() { if (!frame) frame = requestAnimationFrame(() => { frame = 0; measure() }) }
function measure() {
  const r = current.value
  const el = r && q(r.selector)
  if (!open.value || !el) return
  const view = { width: window.innerWidth, height: window.innerHeight }
  const b = el.getBoundingClientRect()
  const clipped = clipRect({ top: b.top, left: b.left, width: b.width, height: b.height }, view)
  hole.value = clipped
  const size = tip.value?.getBoundingClientRect() ?? { width: Math.min(352, view.width - 24), height: 180 }
  const p = placeTooltip(clipped, { width: size.width, height: size.height }, view)
  tipPos.value = { top: p.top, left: p.left }
}

function next() { if (last.value) void close(); else void go(index.value + 1) }
function prev() { if (index.value > 0) void go(index.value - 1) }

function finishOverlay() { releaseOverlay(COMPETE_GUIDE_POPUP) }
async function close() {
  if (!open.value) return
  open.value = false // marks the guide seen (useSeenWhileOpen)
  document.removeEventListener('keydown', onKey, true)
  window.removeEventListener('scroll', schedule)
  window.removeEventListener('resize', schedule)
  hole.value = null; tipPos.value = null
  finishOverlay()
  await showTab(startTab)
  const back = returnFocus && returnFocus !== document.body && document.contains(returnFocus) ? returnFocus : q('[data-testid="compete-guide-open"]')
  back?.focus({ preventScroll: true })
}

function onKey(e: KeyboardEvent) {
  if (!open.value) return
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); void close(); return }
  const typing = e.target instanceof HTMLElement && e.target.closest('input, textarea, select')
  if (!typing && e.key === 'ArrowRight') { e.preventDefault(); next() }
  else if (!typing && e.key === 'ArrowLeft') { e.preventDefault(); prev() }
  else if (e.key === 'Tab' && tip.value) {
    // Keep focus inside the tooltip while the page is dimmed.
    const items = [...tip.value.querySelectorAll<HTMLElement>('button:not([disabled])')]
    if (!items.length) return
    const first = items[0]!, end = items[items.length - 1]!
    const inside = tip.value.contains(document.activeElement)
    if (e.shiftKey && (document.activeElement === first || !inside)) { e.preventDefault(); end.focus() }
    else if (!e.shiftKey && (document.activeElement === end || !inside)) { e.preventDefault(); first.focus() }
  }
}

// The layout appears after the workspace loads; wait for its tabs (only the simplified layout has them).
let poll: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  let tries = 0
  poll = setInterval(async () => {
    if (++tries > 60) { clearInterval(poll); return }
    if (!q('[data-testid="compete-tabs"]')) return
    clearInterval(poll)
    available.value = true
    if (shouldAutoShow('v2', await seenKeys(), !!location.hash) && !disposed) { autoRequested.value = true; requestOverlay(COMPETE_GUIDE_POPUP) }
  }, 250)
})
// Its turn as this page load's popup.
watch(() => autoRequested.value && overlayActive(COMPETE_GUIDE_POPUP), turn => { if (turn && !disposed) void start() })
onUnmounted(() => {
  disposed = true
  clearInterval(poll)
  if (frame) cancelAnimationFrame(frame)
  document.removeEventListener('keydown', onKey, true)
  window.removeEventListener('scroll', schedule)
  window.removeEventListener('resize', schedule)
  finishOverlay()
})
</script>

<template>
  <button v-if="available" type="button" class="btn sm guide-open" data-testid="compete-guide-open" @click="autoRequested = false; start()">
    <span aria-hidden="true">?</span> {{ ui.open }}
  </button>
  <Teleport to="body">
    <div v-if="open && current && copy" class="guide-root" data-testid="compete-guide">
      <div class="guide-block" aria-hidden="true" @click.stop />
      <div v-if="hole" class="guide-hole" aria-hidden="true"
        :style="{ top: hole.top + 'px', left: hole.left + 'px', width: hole.width + 'px', height: hole.height + 'px' }" />
      <div ref="tip" class="guide-tip" role="dialog" aria-modal="true" aria-labelledby="guide-title" aria-describedby="guide-body"
        :data-step="current.step.id" :style="tipPos ? { top: tipPos.top + 'px', left: tipPos.left + 'px' } : { visibility: 'hidden' }">
        <p class="guide-count" data-testid="compete-guide-count">{{ index + 1 }} / {{ steps.length }}</p>
        <h2 id="guide-title" class="guide-title">{{ copy.title }}</h2>
        <p id="guide-body" class="guide-body">{{ copy.body }}</p>
        <div class="guide-actions">
          <button type="button" class="guide-skip" data-testid="compete-guide-skip" @click="close">{{ ui.skip }}</button>
          <span class="guide-grow" />
          <button v-if="index > 0" type="button" class="btn sm" data-testid="compete-guide-prev" @click="prev">{{ ui.prev }}</button>
          <button ref="nextBtn" type="button" class="btn sm primary" data-testid="compete-guide-next" @click="next">{{ last ? ui.done : ui.next }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.guide-open { white-space: nowrap; }
.guide-open span { display: inline-grid; place-items: center; width: 1.1rem; height: 1.1rem; margin-right: .2rem; border: 1px solid currentColor; border-radius: 50%; font-size: .7rem; line-height: 1; }
.guide-root { position: fixed; inset: 0; z-index: 300; }
.guide-block { position: fixed; inset: 0; }
.guide-hole { position: fixed; border-radius: 8px; box-shadow: 0 0 0 2px rgba(158,173,255,.9), 0 0 0 200vmax rgba(2,5,14,.72); pointer-events: none;
  transition: top .25s ease, left .25s ease, width .25s ease, height .25s ease; }
.guide-tip { position: fixed; width: min(22rem, calc(100vw - 1.5rem)); padding: 1rem 1.1rem .9rem; border: 1px solid rgba(158,173,255,.45);
  border-radius: 8px; background: #0b1022; color: #e8ecf8; box-shadow: 0 18px 60px rgba(0,0,0,.55); overflow-wrap: anywhere;
  transition: top .25s ease, left .25s ease; }
.guide-count { font-size: .75rem; letter-spacing: .04em; color: #aeb6c8; }
.guide-title { margin-top: .25rem; font-size: 1.05rem; font-weight: 600; line-height: 1.35; color: #f5f7ff; }
.guide-body { margin-top: .4rem; font-size: .92rem; line-height: 1.55; color: #cfd5e4; }
.guide-actions { display: flex; align-items: center; gap: .5rem; margin-top: .9rem; }
.guide-grow { flex: 1; }
.guide-skip { padding: .4rem .2rem; font-size: .85rem; color: #aeb6c8; background: none; border: 0; cursor: pointer; text-decoration: underline; text-underline-offset: 3px; }
.guide-skip:hover { color: #fff; }
.guide-tip :focus-visible, .guide-skip:focus-visible { outline: 2px solid #9eadff; outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { .guide-hole, .guide-tip { transition: none; } }
</style>
