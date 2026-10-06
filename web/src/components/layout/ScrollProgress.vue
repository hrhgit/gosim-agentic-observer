<script setup lang="ts">
/** A one-pixel bar under the header showing how far down the current page you are. */
import { onMounted, onUnmounted, ref } from 'vue'

const progress = ref(0)
let frame = 0

function measure() {
  const doc = document.documentElement
  const scrollable = doc.scrollHeight - window.innerHeight
  progress.value = scrollable > 40 ? Math.min(1, Math.max(0, window.scrollY / scrollable)) : 0
}

function onScroll() {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(measure)
}

onMounted(() => {
  measure()
  window.addEventListener('scroll', onScroll, { passive: true })
  window.addEventListener('resize', onScroll, { passive: true })
})
onUnmounted(() => {
  cancelAnimationFrame(frame)
  window.removeEventListener('scroll', onScroll)
  window.removeEventListener('resize', onScroll)
})
</script>

<template>
  <div class="scroll-progress" aria-hidden="true"><i :style="{ transform: `scaleX(${progress})` }"></i></div>
</template>

<style scoped>
.scroll-progress {
  position: fixed;
  top: 0; left: 0; right: 0;
  z-index: 60;
  height: 2px;
  pointer-events: none;
}
.scroll-progress i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #315efb, #78a6ff);
  transform-origin: left;
  transition: transform .12s linear;
}
@media (prefers-reduced-motion: reduce) {
  .scroll-progress i { transition: none; }
}
</style>
