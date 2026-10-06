<script setup lang="ts">
import { useI18n } from '../../composables/useI18n'
import type { Scenario } from '../../lib/data'
import { scenarioLabel } from '../../lib/scenarioLabels'

// Practice scores on different scenarios are not comparable (14 nights against 180), so the board ranks one at a time.
defineProps<{ scenarios: Scenario[]; modelValue: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [slug: string] }>()
const { t, tf, locale } = useI18n()
</script>

<template>
  <div v-if="scenarios.length" class="board-scenarios" role="group" :aria-label="t('leaderboard.scenarios')" data-testid="board-scenarios">
    <button
      v-for="s in scenarios" :key="s.id" type="button"
      :class="{ active: s.slug === modelValue }" :aria-pressed="s.slug === modelValue" :title="s.slug"
      :data-testid="`board-scenario-${s.slug}`"
      @click="emit('update:modelValue', s.slug)"
    >{{ scenarioLabel(s.slug, s.name, locale) }}<small v-if="s.n_nights"> · {{ tf('leaderboard.nights', { n: s.n_nights }) }}</small></button>
  </div>
</template>

<style scoped>
.board-scenarios { display: flex; flex-wrap: wrap; gap: .5rem; }
.board-scenarios button {
  border: 1px solid rgba(255,255,255,.2); background: rgba(6,6,7,.6); color: #b5b5b5;
  padding: .45rem .8rem; cursor: pointer;
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .75rem; letter-spacing: .04em;
  transition: border-color .2s ease, color .2s ease, background-color .2s ease;
}
.board-scenarios button small { font-size: inherit; color: #858585; }
.board-scenarios button:hover { border-color: #78a6ff; color: #fff; }
.board-scenarios button.active { border-color: #315efb; background: rgba(49,94,251,.16); color: #fff; }
.board-scenarios button:focus-visible { outline: 2px solid #78a6ff; outline-offset: 2px; }
</style>
