<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { cardBoardTabs, type BoardCard, type BoardLayout } from '../../lib/data'
import { scenarioLabel } from '../../lib/scenarioLabels'

// Card boards: one tab per card (labelled from the card slug, localized — see scenarioLabels.ts) and,
// where the phase ranks an overall mean, an Overall tab first. null stands for the overall tab.
const props = defineProps<{ layout: BoardLayout; cards: BoardCard[]; modelValue: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [slug: string | null] }>()
const { t, locale } = useI18n()
const tabs = computed(() => cardBoardTabs({ layout: props.layout, cards: props.cards })
  .map(slug => ({ slug, label: slug === null ? t('leaderboard.overall') : scenarioLabel(slug, props.cards.find(c => c.slug === slug)?.name ?? slug, locale.value) })))
</script>

<template>
  <div v-if="tabs.length" class="board-cards" role="group" :aria-label="t('leaderboard.cards')" data-testid="board-cards">
    <button
      v-for="tab in tabs" :key="tab.slug ?? ''" type="button"
      :class="{ active: tab.slug === modelValue, overall: tab.slug === null }" :aria-pressed="tab.slug === modelValue"
      :data-testid="tab.slug === null ? 'board-card-overall' : `board-card-${tab.slug}`"
      @click="emit('update:modelValue', tab.slug)"
    >{{ tab.label }}</button>
  </div>
</template>

<style scoped>
.board-cards { display: flex; flex-wrap: wrap; gap: .5rem; }
.board-cards button {
  border: 1px solid rgba(255,255,255,.2); background: rgba(6,6,7,.6); color: #b5b5b5;
  padding: .45rem .8rem; cursor: pointer;
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .75rem; letter-spacing: .04em;
  transition: border-color .2s ease, color .2s ease, background-color .2s ease;
}
.board-cards button.overall { text-transform: uppercase; letter-spacing: .1em; }
.board-cards button:hover { border-color: #78a6ff; color: #fff; }
.board-cards button.active { border-color: #315efb; background: rgba(49,94,251,.16); color: #fff; }
.board-cards button:focus-visible { outline: 2px solid #78a6ff; outline-offset: 2px; }
</style>
