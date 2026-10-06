<script setup lang="ts">
import { computed } from 'vue'
import UserAvatar from '../UserAvatar.vue'
import { useI18n } from '../../composables/useI18n'
import type { BoardCard, BoardLayout, LeaderboardEntry } from '../../lib/data'
import { num } from '../../lib/format'
import { scenarioLabel } from '../../lib/scenarioLabels'

// The table of a card board. Overall tab: the mean and one column per card. Card tab: the card score, the
// team's overall score where the phase has one, and whatever numeric score components the runs report.
const props = defineProps<{ entries: LeaderboardEntry[]; layout: BoardLayout; cards: BoardCard[]; tab: string | null; teamId: string | null }>()
const emit = defineEmits<{ select: [entry: LeaderboardEntry] }>()
const { t, tf, locale } = useI18n()

const overallTab = computed(() => props.tab === null)
const componentKeys = computed(() => {
  if (overallTab.value) return []
  const keys: string[] = []
  for (const e of props.entries) for (const k of Object.keys(e.components ?? {})) if (!keys.includes(k)) keys.push(k)
  return keys
})
function componentLabel(key: string) {
  const label = t(`leaderboard.components.${key}`)
  return typeof label === 'string' && label !== `leaderboard.components.${key}` ? label : key.replace(/_/g, ' ')
}
const has = (field: 'required_missing' | 'targets_observed' | 'completed_tiles') => props.entries.some(e => e[field] != null)
const showMissing = computed(() => !overallTab.value && has('required_missing'))
const showTargets = computed(() => !overallTab.value && has('targets_observed'))
const showTiles = computed(() => !overallTab.value && has('completed_tiles'))
const showOverall = computed(() => !overallTab.value && props.layout === 'cards_overall')
// The final-version mark under the score (formal phase where teams choose one): its score, or the default.
function finalMark(row: LeaderboardEntry) {
  const f = row.final_version
  if (!f) return ''
  if (!f.chosen) return t('leaderboard.final_version_default')
  return f.score == null ? t('leaderboard.final_version_unscored') : tf('leaderboard.final_version', { score: num(f.score) })
}
// The lowest-highest of the averaged evaluations (the hidden final's 3 runs).
const rangeText = (r: [number, number]) => tf('leaderboard.range', { low: num(r[0]), high: num(r[1]) })
const signed = (value: number) => `${value < 0 ? '−' : ''}${num(Math.abs(value))}`
</script>

<template>
  <div class="table-wrap" data-testid="card-board">
    <table class="data-table">
      <thead><tr>
        <th>{{ t('leaderboard.rank') }}</th><th>{{ t('leaderboard.team') }}</th>
        <th class="r">{{ overallTab ? t('leaderboard.overall') : t('leaderboard.score') }}</th>
        <template v-if="overallTab"><th v-for="c in cards" :key="c.slug" class="r" :data-testid="`card-col-${c.slug}`">{{ scenarioLabel(c.slug, c.name, locale) }}</th></template>
        <th v-if="showOverall" class="r">{{ t('leaderboard.overall') }}</th>
        <th v-for="k in componentKeys" :key="k" class="r">{{ componentLabel(k) }}</th>
        <th v-if="showTiles" class="r">{{ t('leaderboard.tiles') }}</th>
        <th v-if="showTargets" class="r">{{ t('leaderboard.targets_observed') }}</th>
        <th v-if="showMissing" class="r">{{ t('leaderboard.required_missing') }}</th>
        <th class="r">{{ t('leaderboard.submissions') }}</th>
      </tr></thead>
      <tbody>
        <tr v-for="row in entries" :key="row.team_id" data-testid="lb-row" class="lb-click" :class="{ me: teamId === row.team_id }" tabindex="0"
            @click="emit('select', row)" @keydown.enter.prevent="emit('select', row)">
          <td class="m rank-cell" :class="row.rank <= 3 ? `rank-${row.rank}` : ''">{{ row.rank }}</td>
          <td><span class="team-cell"><UserAvatar :name="row.team_name" :github="row.leader_github" :avatar-url="row.leader_avatar_url" /><i v-if="row.rank === 1" class="champ-star" aria-hidden="true">✦</i><span class="team-name">{{ row.team_name }}</span></span><span v-if="teamId === row.team_id" class="label accent ml-2">{{ t('leaderboard.me') }}</span></td>
          <td class="r m" :class="{ 'text-[#ff6b6b]': row.total_score < 0 }">{{ num(row.total_score) }}<small v-if="!overallTab && row.unfinished" class="unfinished" :title="t('leaderboard.unfinished_help')" data-testid="card-unfinished">{{ t('leaderboard.unfinished') }}</small><small v-if="row.averaged_runs" class="sub" :title="tf('leaderboard.averaged_runs_help', { n: row.averaged_runs })" data-testid="averaged-runs">{{ tf('leaderboard.averaged_runs', { n: row.averaged_runs }) }}</small><small v-if="row.score_range" class="sub" :title="tf('leaderboard.range_help', { n: row.averaged_runs ?? 0 })" data-testid="score-range">{{ rangeText(row.score_range) }}</small><small v-if="row.final_version" class="sub" :class="{ chosen: row.final_version.chosen }" :title="t('leaderboard.final_version_help')" data-testid="final-version-mark">{{ finalMark(row) }}</small></td>
          <template v-if="overallTab"><td v-for="c in cards" :key="c.slug" class="r m">{{ row.card_scores?.[c.slug] == null ? '—' : num(row.card_scores[c.slug]!) }}<small v-if="row.unfinished_cards?.includes(c.slug)" class="unfinished" :title="t('leaderboard.unfinished_help')" data-testid="card-unfinished">{{ t('leaderboard.unfinished') }}</small><small v-if="row.card_ranges?.[c.slug]" class="sub" :title="tf('leaderboard.range_help', { n: row.averaged_runs ?? 0 })" data-testid="card-range">{{ rangeText(row.card_ranges[c.slug]!) }}</small></td></template>
          <td v-if="showOverall" class="r m">{{ row.overall_score == null ? '—' : num(row.overall_score) }}<small v-if="row.overall_rank" class="text3"> · #{{ row.overall_rank }}</small></td>
          <td v-for="k in componentKeys" :key="k" class="r m" :class="{ 'text-[#ff6b6b]': (row.components?.[k] ?? 0) < 0 }">{{ row.components?.[k] == null ? '—' : signed(row.components[k]!) }}</td>
          <td v-if="showTiles" class="r m">{{ row.completed_tiles ?? '—' }}</td>
          <td v-if="showTargets" class="r m">{{ row.targets_observed ?? '—' }}</td>
          <td v-if="showMissing" class="r m" :class="{ 'text-[#ff6b6b]': Number(row.required_missing) > 0 }">{{ row.required_missing ?? '—' }}</td>
          <td class="r m">{{ row.submission_count }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.lb-click { cursor: pointer; transition: background-color .2s ease; }
.lb-click:hover { background: rgba(49,94,251,.08); }
.lb-click:focus-visible { outline: 2px solid #78a6ff; outline-offset: -2px; }
.unfinished { margin-left: .4em; font-size: .75em; color: #ff9b6b; white-space: nowrap; }
/* Secondary lines under the score: wrap inside the cell instead of widening the table on phones. */
.sub { display: block; margin-left: auto; max-width: 12em; font-size: .72em; line-height: 1.3; color: #9aa0a6; white-space: normal; }
.sub.chosen { color: #78a6ff; }
</style>
