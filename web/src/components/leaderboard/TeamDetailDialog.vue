<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import UserAvatar from '../UserAvatar.vue'
import { useI18n } from '../../composables/useI18n'
import type { BoardCard, LeaderboardEntry } from '../../lib/data'
import { fmtUtc, githubHandle, num, pct } from '../../lib/format'
import { scenarioLabel } from '../../lib/scenarioLabels'

// One team's line on the board, opened from the chart or the table. Only what the board already
// publishes is shown: team names, scores and ranks are public; member details are not.
// Card boards pass their cards and the current tab's label; their rows carry numeric score components.
const props = defineProps<{ entry: LeaderboardEntry | null; mine: boolean; cards?: BoardCard[]; boardLabel?: string | null }>()
const emit = defineEmits<{ close: [] }>()
const { t, tf, pick, locale } = useI18n()
const closeBtn = ref<HTMLButtonElement | null>(null)

const handle = computed(() => githubHandle(props.entry?.leader_github))
const parts = computed(() => {
  const e = props.entry
  if (!e) return []
  if (e.components && Object.keys(e.components).length) return Object.entries(e.components).map(([key, value]) => {
    const label = t(`leaderboard.components.${key}`)
    return { key, label: typeof label === 'string' && label !== `leaderboard.components.${key}` ? label : key.replace(/_/g, ' '), value }
  })
  const rows = [
    { key: 'base', label: t('leaderboard.base_science'), value: e.base_science },
    { key: 'bonus', label: t('leaderboard.chart.legend_bonus'), value: e.program_bonus },
    { key: 'request', label: t('leaderboard.chart.legend_request'), value: e.request_reward },
  ]
  if (e.coverage_bonus != null && e.coverage_bonus !== 0) rows.push({ key: 'coverage', label: t('leaderboard.coverage'), value: e.coverage_bonus })
  if (e.report_reward) rows.push({key:'reports',label:pick('Anomaly reports','异常报告奖励'),value:e.report_reward})
  rows.push({ key: 'penalty', label: t('leaderboard.penalties'), value: -Math.abs(e.penalty_total) })
  return rows
})
const scale = computed(() => Math.max(1, ...parts.value.map(p => Math.abs(p.value))))
const cardScores = computed(() => (props.cards ?? []).filter(c => props.entry?.card_scores?.[c.slug] != null)
  .map(c => ({ ...c, label: scenarioLabel(c.slug, c.name, locale.value), score: props.entry!.card_scores![c.slug]!, unfinished: !!props.entry!.unfinished_cards?.includes(c.slug) })))
const board = computed(() => props.boardLabel ? props.boardLabel : props.entry?.scenario_slug ? tf('leaderboard.detail.board_scenario', { scenario: props.entry.scenario_slug }) : t('leaderboard.detail.board_mean'))

function onKey(event: KeyboardEvent) { if (event.key === 'Escape') emit('close') }
watch(() => props.entry, async (entry) => {
  document.documentElement.style.overflow = entry ? 'hidden' : ''
  if (entry) { window.addEventListener('keydown', onKey); await nextTick(); closeBtn.value?.focus() }
  else window.removeEventListener('keydown', onKey)
})
onUnmounted(() => { document.documentElement.style.overflow = ''; window.removeEventListener('keydown', onKey) })
</script>

<template>
  <Teleport to="body">
  <div v-if="entry" class="team-detail" data-testid="team-detail" @click.self="emit('close')">
    <section class="team-detail-panel" role="dialog" aria-modal="true" :aria-label="entry.team_name">
      <header class="team-detail-head">
        <span class="team-detail-rank" :class="entry.rank <= 3 ? `rank-${entry.rank}` : ''">#{{ entry.rank }}</span>
        <UserAvatar :name="entry.team_name" :github="entry.leader_github" :avatar-url="entry.leader_avatar_url" />
        <h2 class="team-detail-name">{{ entry.team_name }}</h2>
        <span v-if="mine" class="team-detail-tag">{{ t('leaderboard.chart.your_team') }}</span>
        <button ref="closeBtn" type="button" class="team-detail-close" :aria-label="t('leaderboard.detail.close')" data-testid="team-detail-close" @click="emit('close')">×</button>
      </header>
      <p class="team-detail-board">{{ board }}</p>

      <div class="team-detail-total">
        <span class="label">{{ t(entry.calibrated ? 'leaderboard.calibrated_score' : 'leaderboard.score') }}<template v-if="entry.unfinished"> · <span :title="t('leaderboard.unfinished_help')">{{ t('leaderboard.unfinished') }}</span></template></span>
        <b :class="{ neg: entry.total_score < 0 }">{{ num(entry.total_score) }}</b>
      </div>

      <p v-if="entry.calibrated" class="team-detail-board">{{ t('leaderboard.calibration_note') }}<br>{{ t('leaderboard.raw_score') }}: {{ num(entry.raw_total_score ?? 0) }}</p>
      <ul class="team-detail-parts">
        <li v-for="p in parts" :key="p.key" :class="p.key">
          <span class="part-label">{{ p.label }}</span>
          <span class="part-track"><i :style="{ width: `${(Math.abs(p.value) / scale * 100).toFixed(2)}%` }"></i></span>
          <span class="part-value">{{ p.value < 0 ? '−' : p.key === 'base' ? '' : '+' }}{{ num(Math.abs(p.value)) }}</span>
        </li>
      </ul>

      <dl class="team-detail-stats">
        <div v-for="c in cardScores" :key="c.slug" data-testid="team-detail-card"><dt>{{ c.label }}</dt><dd>{{ num(c.score) }}<small v-if="c.unfinished" :title="t('leaderboard.unfinished_help')"> · {{ t('leaderboard.unfinished') }}</small></dd></div>
        <div v-if="entry.overall_score != null && entry.scenario_slug"><dt>{{ t('leaderboard.overall') }}</dt><dd>{{ num(entry.overall_score) }}<template v-if="entry.overall_rank"> · #{{ entry.overall_rank }}</template></dd></div>
        <div v-if="entry.completed_tiles != null || !entry.components"><dt>{{ t('leaderboard.tiles') }}</dt><dd>{{ entry.completed_tiles ?? '—' }}</dd></div>
        <div v-if="entry.targets_observed != null"><dt>{{ t('leaderboard.targets_observed') }}</dt><dd>{{ entry.targets_observed }}</dd></div>
        <div><dt>{{ t('leaderboard.required_missing') }}</dt><dd :class="{ neg: Number(entry.required_missing) > 0 }">{{ entry.required_missing ?? '—' }}</dd></div>
        <div v-if="entry.kind !== 'observer'"><dt>{{ t('leaderboard.completion') }}</dt><dd>{{ pct(entry.completion_rate) }}</dd></div>
        <div v-if="entry.coverage_evenness != null"><dt>{{ t('leaderboard.detail.evenness') }}</dt><dd>{{ num(entry.coverage_evenness, 3) }}</dd></div>
        <div><dt>{{ t('leaderboard.submissions') }}</dt><dd>{{ entry.submission_count }}</dd></div>
        <div><dt>{{ t('leaderboard.detail.scored_at') }}</dt><dd>{{ entry.scored_at ? `${fmtUtc(entry.scored_at)} UTC` : '—' }}</dd></div>
      </dl>

      <div v-if="handle || (mine && (entry.best_submission_id || entry.observer_batch_id))" class="team-detail-links">
        <a v-if="handle" :href="`https://github.com/${handle}`" target="_blank" rel="noopener noreferrer">{{ tf('leaderboard.detail.github', { handle }) }} ↗</a>
        <router-link v-if="mine && entry.best_submission_id" :to="`/submissions/${entry.best_submission_id}`" @click="emit('close')">{{ t('leaderboard.detail.view_submission') }} →</router-link>
        <router-link v-if="mine && entry.observer_batch_id" to="/compete" @click="emit('close')">{{ pick('View evaluation','查看评测') }} →</router-link>
      </div>
    </section>
  </div>
  </Teleport>
</template>

<style scoped>
.team-detail {
  position: fixed; inset: 0; z-index: 150;
  display: flex; align-items: center; justify-content: center;
  padding: 1.25rem; overflow-y: auto;
  background: rgba(3, 4, 8, .72); backdrop-filter: blur(6px);
}
.team-detail-panel {
  width: 100%; max-width: 34rem; margin: auto;
  border: 1px solid rgba(255,255,255,.2); background: #0a0c14;
  box-shadow: 0 30px 80px rgba(0,0,0,.55);
  font-variant-numeric: tabular-nums;
}
.team-detail-head { display: flex; align-items: center; gap: .7rem; padding: 1.1rem 1.25rem .4rem; }
.team-detail-rank { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .85rem; color: #315efb; }
.team-detail-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 1.25rem; font-weight: 600; color: #f5f5f5; }
.team-detail-tag { flex-shrink: 0; border: 1px solid #315efb; padding: .05rem .35rem; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .6rem; letter-spacing: .1em; color: #78a6ff; }
.team-detail-close {
  margin-left: auto; flex-shrink: 0; width: 2.2rem; height: 2.2rem; border: 1px solid rgba(255,255,255,.18);
  background: none; color: #d4d4d4; font-size: 1.3rem; line-height: 1; cursor: pointer;
}
.team-detail-close:hover { border-color: #78a6ff; color: #fff; }
.team-detail-close:focus-visible, .team-detail-links a:focus-visible { outline: 2px solid #78a6ff; outline-offset: 2px; }
.team-detail-board { padding: 0 1.25rem; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .7rem; letter-spacing: .08em; color: #858585; }
.team-detail-total { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin: 1rem 1.25rem 0; padding: .9rem 0; border-top: 1px solid rgba(255,255,255,.12); border-bottom: 1px solid rgba(255,255,255,.12); }
.team-detail-total b { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 1.9rem; font-weight: 500; color: #f5f5f5; }
.team-detail-total b.neg, dd.neg { color: #ff6b6b; }
.team-detail-parts { list-style: none; margin: 0; padding: .9rem 1.25rem .4rem; display: grid; gap: .55rem; }
.team-detail-parts li { display: grid; grid-template-columns: 7.5rem minmax(0, 1fr) 6rem; align-items: center; gap: .75rem; font-size: .8rem; color: #d4d4d4; }
.part-track { position: relative; height: 8px; background: rgba(255,255,255,.06); }
.part-track i { position: absolute; top: 0; bottom: 0; left: 0; }
.part-value { text-align: right; font-family: 'IBM Plex Mono', ui-monospace, monospace; color: #f5f5f5; }
li.base i { background: #315efb; }
li.bonus i { background: #78a6ff; }
li.request i { background: #59d78d; }
li.coverage i { background: #c9a7ff; }
li.penalty i { background: repeating-linear-gradient(135deg, rgba(255,107,107,.85) 0 2px, rgba(255,107,107,.18) 2px 5px); }
li.penalty .part-value { color: #ff6b6b; }
.team-detail-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: .8rem 0 0; border-top: 1px solid rgba(255,255,255,.12); }
.team-detail-stats > div { min-width: 0; padding: .7rem 1.25rem; border-bottom: 1px solid rgba(255,255,255,.06); }
.team-detail-stats dt { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .6rem; letter-spacing: .1em; text-transform: uppercase; color: #858585; }
.team-detail-stats dd { margin: .2rem 0 0; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .85rem; color: #f5f5f5; overflow-wrap: anywhere; }
.team-detail-links { display: flex; flex-wrap: wrap; gap: .6rem 1.4rem; padding: .9rem 1.25rem 1.1rem; font-size: .85rem; }
.team-detail-links a { color: #78a6ff; }
.team-detail-links a:hover { color: #fff; }
@media (max-width: 520px) {
  .team-detail-parts li { grid-template-columns: 5.5rem minmax(0, 1fr) 5rem; }
  .team-detail-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
