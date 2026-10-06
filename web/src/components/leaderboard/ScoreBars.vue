<script setup lang="ts">
import UserAvatar from '../UserAvatar.vue'
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import type { LeaderboardEntry } from '../../lib/data'
import { fmtUtc, num } from '../../lib/format'

const props = defineProps<{ entries: LeaderboardEntry[]; teamId: string | null; updatedAt: Date | null }>()
const emit = defineEmits<{ select: [entry: LeaderboardEntry] }>()
const { t, tf } = useI18n()
const top = computed(() => props.entries.slice(0, 10))
const calibrated = computed(() => props.entries.some(e => e.calibrated))
const mine = computed(() => props.teamId ? props.entries.find(e => e.team_id === props.teamId) ?? null : null)
const outside = computed(() => mine.value && !top.value.some(e => e.team_id === mine.value!.team_id) ? mine.value : null)
const gross = (e: LeaderboardEntry) => e.calibrated ? Math.max(0, e.total_score) : Math.max(0, e.base_science) + Math.max(0, e.program_bonus) + Math.max(0, e.request_reward)
const max = computed(() => Math.max(1, ...[...top.value, ...(outside.value ? [outside.value] : [])].map(e => Math.max(gross(e), e.total_score))))
const scoredRuns = computed(() => props.entries.reduce((s, e) => s + e.submission_count, 0))
const widthPct = (v: number) => `${Math.max(0, Math.min(100, (v / max.value) * 100)).toFixed(2)}%`
const isMe = (e: LeaderboardEntry) => props.teamId != null && e.team_id === props.teamId
/** The hatched penalty tail covers the part of the gross bar that the penalties took away (all of it when the total is negative). */
const penaltyLeft = (e: LeaderboardEntry) => widthPct(Math.max(0, Math.min(e.total_score, gross(e))))
const penaltyWidth = (e: LeaderboardEntry) => e.calibrated ? '0%' : widthPct(Math.min(gross(e), Math.max(0, e.penalty_total)))
const tooltip = (e: LeaderboardEntry) => tf('leaderboard.chart.tooltip', { score: num(e.total_score), base: num(e.base_science), bonus: num(e.program_bonus), requests: num(e.request_reward), coverage: num(e.coverage_bonus ?? 0), penalty: num(e.penalty_total), tiles: e.completed_tiles ?? '—', missing: e.required_missing ?? '—' })
</script>

<template>
  <div class="score-bars" data-testid="score-bars">
    <div class="score-bars-head">
      <span class="label">{{ t(calibrated ? 'leaderboard.calibrated_score' : 'leaderboard.chart.title') }}</span>
      <span v-if="!calibrated" class="score-bars-legend" aria-hidden="true"><i class="base"></i>{{ t('leaderboard.chart.legend_base') }} <i class="bonus"></i>{{ t('leaderboard.chart.legend_bonus') }} <i class="request"></i>{{ t('leaderboard.chart.legend_request') }} <i class="penalty"></i>{{ t('leaderboard.chart.penalty') }}</span>
      <span v-else class="text-sm text3">{{ t('leaderboard.calibration_note') }}</span>
    </div>
    <ol class="score-bars-list">
      <li v-for="row in top" :key="row.team_id" class="score-bar-row" :class="{ me: isMe(row) }" data-testid="score-bar" role="button" tabindex="0" @click="emit('select', row)" @keydown.enter.prevent="emit('select', row)" @keydown.space.prevent="emit('select', row)">
        <span class="rank" :class="row.rank <= 3 ? `rank-${row.rank}` : ''">{{ row.rank }}</span>
        <span class="name"><UserAvatar :name="row.team_name" :github="row.leader_github" :avatar-url="row.leader_avatar_url" /><i v-if="row.rank === 1" class="champ-star" aria-hidden="true">✦</i><span class="truncate">{{ row.team_name }}</span><span v-if="isMe(row)" class="tag">{{ t('leaderboard.chart.your_team') }}</span></span>
        <span class="track" :title="tooltip(row)">
          <i class="base" :style="{ width: widthPct(Math.max(0, row.calibrated ? row.total_score : row.base_science)) }"></i>
          <i v-if="!row.calibrated" class="bonus" :style="{ left: widthPct(Math.max(0, row.base_science)), width: widthPct(Math.max(0, row.program_bonus)) }"></i>
          <i v-if="!row.calibrated" class="request" :style="{ left: widthPct(Math.max(0, row.base_science) + Math.max(0, row.program_bonus)), width: widthPct(Math.max(0, row.request_reward)) }"></i>
          <i class="penalty" :style="{ left: penaltyLeft(row), width: penaltyWidth(row) }"></i>
        </span>
        <span class="value" :class="{ neg: row.total_score < 0 }">{{ num(row.total_score) }}</span>
        <span class="meta">{{ row.completed_tiles ?? '—' }} {{ t('leaderboard.chart.tiles') }} · {{ row.required_missing ?? '—' }} {{ t('leaderboard.chart.req_missing') }}</span>
      </li>
    </ol>
    <div v-if="outside" class="score-bars-outside">
      <span class="label">{{ t('leaderboard.chart.your_position') }}</span>
      <ol class="score-bars-list">
        <li class="score-bar-row me" data-testid="score-bar-me" role="button" tabindex="0" @click="emit('select', outside)" @keydown.enter.prevent="emit('select', outside)" @keydown.space.prevent="emit('select', outside)">
          <span class="rank" :class="outside.rank <= 3 ? `rank-${outside.rank}` : ''">{{ outside.rank }}</span>
          <span class="name"><UserAvatar :name="outside.team_name" :github="outside.leader_github" :avatar-url="outside.leader_avatar_url" /><span class="truncate">{{ outside.team_name }}</span><span class="tag">{{ t('leaderboard.chart.your_team') }}</span></span>
          <span class="track" :title="tooltip(outside)">
            <i class="base" :style="{ width: widthPct(Math.max(0, outside.calibrated ? outside.total_score : outside.base_science)) }"></i>
            <i v-if="!outside.calibrated" class="bonus" :style="{ left: widthPct(Math.max(0, outside.base_science)), width: widthPct(Math.max(0, outside.program_bonus)) }"></i>
            <i v-if="!outside.calibrated" class="request" :style="{ left: widthPct(Math.max(0, outside.base_science) + Math.max(0, outside.program_bonus)), width: widthPct(Math.max(0, outside.request_reward)) }"></i>
            <i class="penalty" :style="{ left: penaltyLeft(outside), width: penaltyWidth(outside) }"></i>
          </span>
          <span class="value" :class="{ neg: outside.total_score < 0 }">{{ num(outside.total_score) }}</span>
          <span class="meta">{{ outside.completed_tiles ?? '—' }} {{ t('leaderboard.chart.tiles') }} · {{ outside.required_missing ?? '—' }} {{ t('leaderboard.chart.req_missing') }}</span>
        </li>
      </ol>
    </div>
    <dl class="score-bars-stats">
      <div><dt>{{ t('leaderboard.chart.teams_on_board') }}</dt><dd>{{ entries.length }}</dd></div>
      <div><dt>{{ t('leaderboard.chart.scored_runs') }}</dt><dd>{{ scoredRuns }}</dd></div>
      <div><dt>{{ t('leaderboard.chart.last_update') }}</dt><dd>{{ updatedAt ? `${fmtUtc(updatedAt.toISOString(), { seconds: true })} UTC` : '—' }}</dd></div>
    </dl>
  </div>
</template>

<style scoped>
.score-bars { border: 1px solid rgba(255,255,255,.2); background: rgba(6,6,7,.6); font-variant-numeric: tabular-nums; }
.score-bars-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .5rem 1rem; padding: .85rem 1rem; border-bottom: 1px solid rgba(255,255,255,.12); }
.score-bars-legend { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .66rem; letter-spacing: .1em; text-transform: uppercase; color: #858585; }
.score-bars-legend i { display: inline-block; width: .8rem; height: .5rem; margin: 0 .35rem 0 .6rem; vertical-align: middle; }
.score-bars-list { list-style: none; margin: 0; padding: .5rem 1rem; }
.score-bar-row {
  display: grid; grid-template-columns: 2rem minmax(6rem, 11rem) minmax(0, 1fr) 5.5rem; grid-template-areas: 'rank name track value' 'rank meta track value'; align-items: center; gap: .1rem .75rem;
  padding: .45rem .25rem; border-bottom: 1px solid rgba(255,255,255,.06); border-left: 2px solid transparent;
}
.score-bar-row:last-child { border-bottom: 0; }
.score-bar-row.me { border-left-color: #315efb; background: rgba(49,94,251,.1); }
.rank { grid-area: rank; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .75rem; color: #315efb; }
.name { grid-area: name; display: flex; align-items: center; gap: .5rem; min-width: 0; font-size: .875rem; color: #f5f5f5; }
.name .truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { grid-area: meta; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .6rem; letter-spacing: .06em; text-transform: uppercase; color: #858585; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tag { flex-shrink: 0; border: 1px solid #315efb; padding: .05rem .35rem; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .56rem; letter-spacing: .1em; text-transform: uppercase; color: #78a6ff; }
.track { grid-area: track; position: relative; height: 10px; background: rgba(255,255,255,.06); overflow: hidden; }
.track i { position: absolute; top: 0; bottom: 0; transition: width .6s cubic-bezier(.16,1,.3,1), left .6s cubic-bezier(.16,1,.3,1); }
/* a new score rearranges the bars instead of snapping; the leader's bar keeps a slow highlight travelling over it */
.score-bar-row:first-child .track i.base::after {
  content: ''; position: absolute; inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.4), transparent);
  animation: bar-sheen 3.4s ease-in-out infinite;
}
@keyframes bar-sheen { 0% { transform: translateX(-100%); } 55%, 100% { transform: translateX(100%); } }
.score-bar-row { transition: background-color .25s ease; }
.score-bar-row { cursor: pointer; }
.score-bar-row:hover { background: rgba(255,255,255,.04); }
.score-bar-row:focus-visible { outline: 2px solid #78a6ff; outline-offset: -2px; }
@media (prefers-reduced-motion: reduce) {
  .track i { transition: none; }
  .score-bar-row:first-child .track i.base::after { animation: none; display: none; }
}
i.base { left: 0; background: #315efb; }
i.bonus { background: #78a6ff; }
i.request { background: #59d78d; }
i.penalty { background: repeating-linear-gradient(135deg, rgba(255,107,107,.85) 0 2px, rgba(255,107,107,.18) 2px 5px); }
.score-bars-legend i.penalty { background: repeating-linear-gradient(135deg, rgba(255,107,107,.85) 0 2px, rgba(255,107,107,.2) 2px 5px); }
.value { grid-area: value; text-align: right; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .8rem; color: #f5f5f5; }
.value.neg { color: #ff6b6b; }
.score-bars-outside { border-top: 1px dashed rgba(255,255,255,.2); padding-top: .6rem; }
.score-bars-outside > .label { display: block; padding: 0 1rem; }
.score-bars-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; border-top: 1px solid rgba(255,255,255,.12); }
.score-bars-stats > div { min-width: 0; padding: .7rem 1rem; border-right: 1px solid rgba(255,255,255,.08); }
.score-bars-stats > div:last-child { border-right: 0; }
.score-bars-stats dt { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .6rem; letter-spacing: .12em; text-transform: uppercase; color: #858585; }
.score-bars-stats dd { margin: .2rem 0 0; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .8rem; color: #f5f5f5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 640px) {
  .score-bar-row { grid-template-columns: 1.5rem minmax(0, 1fr) 4.5rem; grid-template-areas: 'rank name value' 'rank track track' 'rank meta meta'; row-gap: .3rem; }
  .score-bars-stats { grid-template-columns: 1fr; }
  .score-bars-stats > div { border-right: 0; border-bottom: 1px solid rgba(255,255,255,.08); }
  .score-bars-stats > div:last-child { border-bottom: 0; }
}
</style>
