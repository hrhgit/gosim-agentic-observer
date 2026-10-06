<script setup lang="ts">
/**
 * Counts from 0 up to `value` the first time the element scrolls into view, then tracks later changes
 * (the leaderboard section reloads every 60 s, so the number animates instead of jumping).
 * With `prefers-reduced-motion: reduce` it prints the value directly.
 */
import { onMounted, onUnmounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ value: number; duration?: number; decimals?: number }>(), { duration: 900, decimals: 0 })

const shown = ref(0)
const el = ref<HTMLElement | null>(null)
const seen = ref(false)
let frame = 0

const reduced = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

function animate(from: number, to: number) {
  cancelAnimationFrame(frame)
  if (reduced() || from === to) { shown.value = to; return }
  const start = performance.now()
  const step = (now: number) => {
    const p = Math.min(1, (now - start) / props.duration)
    const eased = 1 - Math.pow(1 - p, 3)
    shown.value = from + (to - from) * eased
    if (p < 1) frame = requestAnimationFrame(step)
    else shown.value = to
  }
  frame = requestAnimationFrame(step)
}

let observer: IntersectionObserver | undefined
onMounted(() => {
  if (!el.value || typeof IntersectionObserver === 'undefined') { seen.value = true; shown.value = props.value; return }
  observer = new IntersectionObserver((entries) => {
    if (!entries.some(e => e.isIntersecting) || seen.value) return
    seen.value = true
    animate(0, props.value)
    observer?.disconnect()
  }, { threshold: 0.4 })
  observer.observe(el.value)
  // safety net: an observer that never fires must not leave a zero on the page
  window.setTimeout(() => { if (!seen.value) { seen.value = true; shown.value = props.value } }, 2500)
})
onUnmounted(() => { observer?.disconnect(); cancelAnimationFrame(frame) })

watch(() => props.value, (to, from) => { if (seen.value) animate(from ?? 0, to) })
</script>

<template>
  <span ref="el" class="tabular-nums">{{ shown.toFixed(decimals) }}</span>
</template>
