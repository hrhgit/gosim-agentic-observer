<script setup lang="ts">
import { useI18n } from '../../composables/useI18n'
const props = defineProps<{ status: string; ns?: string; testid?: string; live?: boolean }>()
const { t } = useI18n()
const label = () => {
  const key = `${props.ns ?? 'status'}.${props.status}`
  const value = t(key)
  return value === key ? props.status : value
}
</script>

<template>
  <span class="pill" :class="status" :data-testid="testid" :aria-live="live ? 'polite' : undefined" :role="live ? 'status' : undefined">
    <i v-if="status === 'queued' || status === 'running'" class="pill-pulse" aria-hidden="true"></i>{{ label() }}
  </span>
</template>

<style scoped>
/* A pill that is still waiting on the evaluator breathes, so an unfinished run is visible at a glance. */
.pill-pulse {
  display: inline-block;
  width: .45rem; height: .45rem;
  margin-right: .45rem;
  border-radius: 999px;
  background: currentColor;
  animation: pill-breathe 1.5s ease-in-out infinite;
}
@keyframes pill-breathe { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: .3; transform: scale(.7); } }
@media (prefers-reduced-motion: reduce) { .pill-pulse { animation: none; } }
</style>
