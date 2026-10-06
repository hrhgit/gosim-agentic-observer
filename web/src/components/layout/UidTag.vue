<script setup lang="ts">
// The signed-in participant's permanent UID, faint in the bottom-right corner of every page. It steps aside
// (fades out) whenever it would sit on the phone register bar, the homepage section rail, the sky console,
// a toast or a popup. Double-click (double-tap on touch) opens a white screen that writes GOSIM CREATE.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../../composables/useI18n'
import { useAuth } from '../../stores/auth'
import { UID_AVOID, boxesOverlap, isDoubleTap, uidLabel } from '../../lib/uid'

const { t } = useI18n()
const route = useRoute()
const { isLoggedIn, me } = useAuth()
const label = computed(() => uidLabel(isLoggedIn.value, me.value?.uid))

const tag = ref<HTMLElement | null>(null)
const dialog = ref<HTMLDialogElement | null>(null)
const blocked = ref(false)
const eggOpen = ref(false)
let openedAt = 0
let lastTap: { t: number; x: number; y: number } | null = null

let frame = 0
function check() {
  frame = 0
  const el = tag.value
  if (!el) return
  const box = el.getBoundingClientRect()
  blocked.value = Array.from(document.querySelectorAll(UID_AVOID)).some(other => boxesOverlap(box, other.getBoundingClientRect(), 6))
}
function schedule() { if (!frame) frame = requestAnimationFrame(check) }

let observer: MutationObserver | null = null
onMounted(() => {
  window.addEventListener('scroll', schedule, { passive: true })
  window.addEventListener('resize', schedule, { passive: true })
  // Bars, toasts and popups come and go without a scroll.
  observer = new MutationObserver(schedule)
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['open'] })
  schedule()
})
onBeforeUnmount(() => {
  window.removeEventListener('scroll', schedule)
  window.removeEventListener('resize', schedule)
  observer?.disconnect()
  if (frame) cancelAnimationFrame(frame)
})
watch([label, () => route.path], () => nextTick(schedule))

async function openEgg() {
  if (eggOpen.value) return
  eggOpen.value = true
  openedAt = performance.now()
  await nextTick()
  if (dialog.value && !dialog.value.open) dialog.value.showModal()
}
function closeEgg() {
  // The tail of the opening double tap must not close it again.
  if (performance.now() - openedAt < 400) return
  if (dialog.value?.open) dialog.value.close()
  eggOpen.value = false
}
function onTap(e: PointerEvent) {
  if (e.pointerType !== 'touch') return
  if (isDoubleTap(lastTap, e.timeStamp, e.clientX, e.clientY)) { lastTap = null; e.preventDefault(); void openEgg() }
  else lastTap = { t: e.timeStamp, x: e.clientX, y: e.clientY }
}
</script>

<template>
  <div v-if="label" ref="tag" class="uid-tag" :class="{ 'is-blocked': blocked }" translate="no" data-testid="uid-tag"
    @dblclick.prevent="openEgg" @pointerup="onTap">{{ label }}</div>
  <dialog v-if="eggOpen" ref="dialog" class="uid-egg" aria-label="GOSIM CREATE" data-testid="uid-egg"
    @cancel="eggOpen = false" @close="eggOpen = false">
    <button type="button" class="uid-egg-stage" :aria-label="t('eggs.mid_autumn.close')" @click="closeEgg">
      <span class="uid-egg-word" aria-hidden="true">GOSIM CREATE</span>
      <span class="uid-egg-rule" aria-hidden="true"></span>
    </button>
  </dialog>
</template>

<style scoped>
.uid-tag {
  position: fixed; z-index: 50;
  right: calc(.65rem + env(safe-area-inset-right, 0px)); bottom: calc(.45rem + env(safe-area-inset-bottom, 0px));
  padding: .1rem .3rem; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .62rem; line-height: 1.2;
  letter-spacing: .08em; color: rgba(205, 214, 238, .32); white-space: nowrap;
  user-select: none; -webkit-user-select: none; touch-action: manipulation; cursor: default;
  transition: opacity .2s ease, visibility .2s;
}
.uid-tag:hover { color: rgba(205, 214, 238, .55); }
:global(.light) .uid-tag { color: rgba(20, 30, 60, .34); }
.uid-tag.is-blocked { opacity: 0; visibility: hidden; pointer-events: none; }

.uid-egg {
  position: fixed; inset: 0; width: 100vw; max-width: none; height: 100dvh; max-height: none; margin: 0; padding: 0;
  border: 0; background: #fff; color: #0b1022; animation: uid-egg-white .35s ease-out both;
}
.uid-egg::backdrop { background: #fff; }
.uid-egg-stage {
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1.4rem;
  width: 100%; height: 100%; border: 0; background: none; cursor: pointer; outline: none;
}
.uid-egg-word {
  padding-left: .32em; letter-spacing: .32em; /* the left pad balances the trailing letter space */
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif; font-weight: 600;
  font-size: clamp(1.7rem, 6.4vw, 5.6rem); line-height: 1; white-space: nowrap;
  background: linear-gradient(100deg, #0b1022 0%, #0b1022 35%, #315efb 50%, #0b1022 65%, #0b1022 100%);
  background-size: 260% 100%; background-position: 0 0;
  -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: uid-egg-word 1.6s cubic-bezier(.2, .7, .1, 1) both;
}
.uid-egg-rule {
  width: min(38vw, 22rem); height: 1px; background: linear-gradient(90deg, transparent, #315efb, transparent);
  transform-origin: center; animation: uid-egg-rule 1.1s .6s cubic-bezier(.2, .7, .1, 1) both;
}
@keyframes uid-egg-white { from { opacity: 0; } to { opacity: 1; } }
@keyframes uid-egg-word {
  /* no 'to': it settles on the element's own values, including the narrower phone spacing */
  from { opacity: 0; letter-spacing: .9em; padding-left: .9em; filter: blur(8px); background-position: 100% 0; }
}
@keyframes uid-egg-rule { from { opacity: 0; transform: scaleX(0); } to { opacity: .7; transform: scaleX(1); } }
@media (max-width: 480px) { .uid-egg-word { letter-spacing: .22em; padding-left: .22em; } }
@media (prefers-reduced-motion: reduce) {
  .uid-egg, .uid-egg-word, .uid-egg-rule { animation: none; }
  .uid-egg-rule { opacity: .7; }
}
</style>
