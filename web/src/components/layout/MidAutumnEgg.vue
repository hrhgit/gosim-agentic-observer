<script setup lang="ts">
// Mid-Autumn Festival greeting: shown around the festival, once per person and year (lib/popupRules).
import { onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { isMidAutumnToday } from '../../lib/eggs'
import { markSeen, seenKeys, useSeenWhileOpen } from '../../stores/popupSeen'

const { t } = useI18n()
const storageKey = `egg-mid-autumn-${new Date().getFullYear()}`
const visible = ref(false)

const seenKey = `egg:${storageKey}`
const finish = useSeenWhileOpen(visible, () => [seenKey])

onMounted(async () => {
  // Automated browsers (tests, screenshot tours) only see it when asked for with ?egg=midautumn.
  const forced = new URLSearchParams(window.location.search).get('egg') === 'midautumn'
  if (!forced && (navigator.webdriver || !isMidAutumnToday())) return
  try { if (localStorage.getItem(storageKey) === 'closed') markSeen([seenKey]) } catch { /* storage may be blocked */ }
  if ((await seenKeys()).has(seenKey)) return
  visible.value = true
})

function close() {
  finish()
  visible.value = false
}
</script>

<template>
  <div v-if="visible" class="mid-autumn" role="status" data-testid="mid-autumn-egg">
    <span class="mid-autumn-moon" aria-hidden="true"></span>
    <strong>{{ t('eggs.mid_autumn.title') }}</strong>
    <button type="button" class="mid-autumn-close" :aria-label="t('eggs.mid_autumn.close')" @click="close">×</button>
  </div>
</template>

<style scoped>
.mid-autumn {
  position: fixed; top: 76px; left: 50%; transform: translateX(-50%); z-index: 60;
  display: flex; align-items: center; gap: .8rem; padding: .7rem 2.6rem .7rem 1rem;
  border: 1px solid rgba(255, 214, 128, .45); border-radius: 12px;
  background: linear-gradient(135deg, rgba(40, 24, 8, .96), rgba(10, 12, 28, .96));
  color: #ffd98a; font-size: 1rem; box-shadow: 0 12px 40px rgba(0, 0, 0, .45), 0 0 32px rgba(255, 200, 90, .18);
  animation: mid-autumn-in .45s ease-out;
}
/* Drawn with CSS so it glows the same on devices without colour emoji fonts. */
.mid-autumn-moon {
  width: 1.8rem; height: 1.8rem; border-radius: 50%; flex-shrink: 0;
  background: radial-gradient(circle at 38% 36%, #fffbe8 0 18%, #ffe7a3 45%, #f5c35c 100%);
  box-shadow: 0 0 18px 4px rgba(255, 214, 120, .55), inset -4px -5px 0 rgba(214, 150, 50, .25);
}
.mid-autumn-close {
  position: absolute; top: 50%; right: .55rem; transform: translateY(-50%); border: 0; background: none;
  color: rgba(255, 244, 220, .7); font-size: 1.3rem; line-height: 1; cursor: pointer;
}
.mid-autumn-close:hover { color: #fff; }
@keyframes mid-autumn-in { from { opacity: 0; transform: translate(-50%, -12px); } to { opacity: 1; transform: translate(-50%, 0); } }
@media (max-width: 640px) { .mid-autumn { top: 68px; } }
@media (prefers-reduced-motion: reduce) { .mid-autumn { animation: none; } }
</style>
