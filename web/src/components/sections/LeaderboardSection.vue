<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { isSupabaseConfigured } from '../../lib/supabase'
import { boardScenarios, isProjectBoard, loadCardBoard, loadLeaderboard, loadParticipantsStats, loadPhases, homeBoardPhase, type CardBoard, type LeaderboardEntry, type Phase } from '../../lib/data'
import { LEADERBOARD_SLUGS, LEADERBOARD_TAB_LABEL_KEYS } from '../../lib/leaderboardBoards'
import { scenarioLabel } from '../../lib/scenarioLabels'
import { useAuth } from '../../stores/auth'
import { fmtUtc, num } from '../../lib/format'
import UserAvatar from '../UserAvatar.vue'
import ScoreBars from '../leaderboard/ScoreBars.vue'
import SkeletonRows from '../layout/SkeletonRows.vue'
import CountUp from '../layout/CountUp.vue'
import BoardScenarioTabs from '../leaderboard/BoardScenarioTabs.vue'
import TeamDetailDialog from '../leaderboard/TeamDetailDialog.vue'
import BoardCardTabs from '../leaderboard/BoardCardTabs.vue'
import CardBoardTable from '../leaderboard/CardBoardTable.vue'

const { t, tf, locale } = useI18n()
const { team } = useAuth()
const phase = ref<Phase | null>(null)
const entries = ref<LeaderboardEntry[]>([])
const hidden = ref(false)
const loading = ref(true)
const refreshing = ref(false)
const error = ref(false)
const teamCount = ref<number | null>(null)
const updatedAt = ref<Date | null>(null)
const selected = ref<LeaderboardEntry | null>(null)
// Practice boards rank one scenario at a time; null on the final board, which averages its scenarios.
const scenarioSlug = ref<string | null>(null)
const scenarioTabs = computed(() => boardScenarios(phase.value))
// Card boards (complete-project phases with board_layout 'cards' / 'cards_overall'): null is the overall tab.
const cardBoard = ref<CardBoard | null>(null)
const cardWanted = ref<string | null>(null)
const cardMode = computed(() => !!cardBoard.value && cardBoard.value.layout !== 'overall' && cardBoard.value.cards.length > 0)
const cardTab = computed(() => cardBoard.value?.scenario ?? null)
const cardLabel = computed(() => cardTab.value === null ? t('leaderboard.detail.board_overall')
  : tf('leaderboard.detail.board_card', { card: scenarioLabel(cardTab.value, cardBoard.value?.cards.find(c => c.slug === cardTab.value)?.name ?? cardTab.value, locale.value) }))
let timer: number | undefined

const top = computed(() => entries.value.slice(0, 10))
const scoredRuns = computed(() => entries.value.reduce((sum, row) => sum + row.submission_count, 0))
const boardLink = computed(() => {
  const scenario = cardMode.value ? cardTab.value : scenarioSlug.value
  return phase.value ? { path: `/leaderboard/${phase.value.slug}`, query: scenario ? { scenario } : {} } : '/leaderboard'
})

async function load() {
  if (!isSupabaseConfigured) { loading.value = false; error.value = true; return }
  refreshing.value = true
  try {
    // Unfiltered: the home mini board must consider every LEADERBOARD_SLUGS phase, not just
    // whichever one teams currently submit to (see usePhases.ts for the same fix on the full page).
    const phases = await loadPhases(true)
    const allowedPhases = phases.filter(p => (LEADERBOARD_SLUGS as readonly string[]).includes(p.slug))
    phase.value = homeBoardPhase(allowedPhases)
    hidden.value = phase.value?.leaderboard_mode === 'hidden'
    if (!scenarioTabs.value.some(s => s.slug === scenarioSlug.value)) scenarioSlug.value = scenarioTabs.value[0]?.slug ?? null
    if (phase.value && !hidden.value && isProjectBoard(phase.value)) {
      cardBoard.value = await loadCardBoard(phase.value.id, cardWanted.value)
      entries.value = cardBoard.value.rows
    } else {
      cardBoard.value = null
      entries.value = phase.value && !hidden.value ? await loadLeaderboard(phase.value.slug, 500, scenarioSlug.value) : []
    }
    updatedAt.value = new Date()
    error.value = false
    loading.value = false
    // Visitors cannot read the teams table, so a direct count came back 0 for everyone not logged in.
    // The public participant stats already count teams; if they fail, fall back to the ranked teams.
    const stats = await loadParticipantsStats().catch(() => null)
    teamCount.value = stats && stats.teams > 0 ? stats.teams : null
  } catch { error.value = true }
  finally { loading.value = false; refreshing.value = false }
}

function pickScenario(slug: string) { scenarioSlug.value = slug; void load() }
function pickCard(slug: string | null) { cardWanted.value = slug; void load() }

function pollLoad() { if (document.visibilityState === 'visible') void load() }
onMounted(() => { load(); timer = window.setInterval(pollLoad, 60_000); document.addEventListener('visibilitychange', pollLoad) })
onUnmounted(() => { if (timer) window.clearInterval(timer); document.removeEventListener('visibilitychange', pollLoad) })
</script>

<template>
  <section id="leaderboard" class="poster-section poster-canvas py-20 md:py-28">
    <div class="relative z-10 mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="grid gap-14 lg:grid-cols-[.54fr_1.46fr] lg:gap-14">
        <div class="reveal lg:sticky lg:top-28 lg:self-start">
          <span class="poster-kicker mt-14">{{ t('home.leaderboard.kicker') }}</span>
          <h2 class="section-title distressed-type mt-9">{{ t('home.leaderboard.title') }}</h2>
          <p class="mt-8 max-w-lg text-base leading-relaxed text-text-secondary md:text-lg">{{ t('home.leaderboard.lede') }}</p>

          <div class="live-strip mt-12" :class="{ 'is-live': entries.length && !error }">
            <span class="live-strip-dot" aria-hidden="true"></span>
            <span class="live-strip-text">{{ entries.length ? t('home.leaderboard.signal_live') : t('home.leaderboard.signal_waiting') }}</span>
            <span v-if="updatedAt" class="live-strip-time">{{ fmtUtc(updatedAt.toISOString(), { seconds: true }) }} UTC</span>
          </div>
          <div class="stats stats-2 reveal-stagger mt-10">
            <div class="stat"><b><CountUp :value="teamCount ?? entries.length" /></b><span>{{ teamCount === null ? t('home.stats_labels.teams_on_board') : t('home.stats_labels.teams') }}</span></div>
            <div class="stat"><b><CountUp :value="scoredRuns" /></b><span>{{ t('home.stats_labels.submissions') }}</span></div>
          </div>
        </div>

        <div class="reveal reveal-delay-1 border-y poster-rule min-w-0 leaderboard-panel">
          <div class="flex flex-wrap items-center justify-between gap-4 border-b poster-rule py-5">
            <span class="font-mono text-xs uppercase tracking-[.1em] text-text-muted">
              <template v-if="phase">{{ t(LEADERBOARD_TAB_LABEL_KEYS[phase.slug]) }}</template>
              <template v-if="updatedAt"> · {{ t('leaderboard.updated') }} {{ fmtUtc(updatedAt.toISOString()) }} UTC</template>
              <template v-else-if="!phase">{{ t('home.leaderboard.feed') }}</template>
            </span>
            <div class="flex gap-5">
              <button type="button" class="font-mono text-xs uppercase tracking-[.1em] text-text-tertiary hover:text-[#315efb] disabled:opacity-50" :disabled="refreshing" @click="load">↻ {{ t('leaderboard.refresh') }}</button>
              <router-link :to="boardLink" class="font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">{{ t('leaderboard.full') }} ↗</router-link>
            </div>
          </div>

          <BoardCardTabs v-if="!hidden && cardMode" class="pt-5" :layout="cardBoard!.layout" :cards="cardBoard!.cards" :model-value="cardTab" @update:model-value="pickCard" />
          <BoardScenarioTabs v-if="!hidden && !cardMode" class="pt-5" :scenarios="scenarioTabs" :model-value="scenarioSlug" @update:model-value="pickScenario" />
          <div v-if="loading" class="py-6"><SkeletonRows :rows="6" :cols="5" :label="t('leaderboard.loading')" /></div>
          <div v-else-if="!entries.length" class="grid min-h-80 place-items-center py-16 text-center">
            <div>
              <div class="empty-zero">00</div>
              <p class="mt-4 max-w-sm text-sm leading-relaxed text-text-secondary">
                {{ hidden ? t('leaderboard.hidden') : error ? t('leaderboard.unavailable') : t('leaderboard.empty') }}
              </p>
            </div>
          </div>

          <div v-else-if="cardMode" class="py-6"><CardBoardTable :entries="top" :layout="cardBoard!.layout" :cards="cardBoard!.cards" :tab="cardTab" :team-id="team?.id ?? null" @select="selected = $event" /></div>
          <template v-else>
            <div class="py-6"><ScoreBars :entries="entries" :team-id="team?.id ?? null" :updated-at="updatedAt" @select="selected = $event" /></div>
            <div class="table-wrap">
              <table class="data-table min-w-[900px]">
                <thead><tr><th>#</th><th>{{ t('leaderboard.team') }}</th><th class="r">{{ t('leaderboard.score') }}</th><th class="r">{{ t('leaderboard.base_science') }}</th><th class="r">{{ t('leaderboard.bonus') }}</th><th class="r">{{ t('leaderboard.requests') }}</th><th class="r">{{ t('leaderboard.penalties') }}</th><th class="r">{{ t('leaderboard.tiles') }}</th><th class="r">{{ t('leaderboard.required_missing') }}</th><th class="r">{{ t('leaderboard.submissions') }}</th></tr></thead>
                <tbody>
                  <tr v-for="row in top" :key="row.team_id" data-testid="lb-row" class="lb-row" :class="{ me: team && team.id === row.team_id }" tabindex="0" @click="selected = row" @keydown.enter.prevent="selected = row">
                    <td class="m rank-cell" :class="row.rank <= 3 ? `rank-${row.rank}` : ''">{{ row.rank }}</td>
                    <td class="font-medium text-text-primary"><span class="team-cell"><UserAvatar :name="row.team_name" :github="row.leader_github" :avatar-url="row.leader_avatar_url" /><i v-if="row.rank === 1" class="champ-star" aria-hidden="true">✦</i><span class="team-name">{{ row.team_name }}</span></span></td>
                    <td class="r m" :class="{ 'text-[#ff6b6b]': row.total_score < 0 }">{{ num(row.total_score) }}</td>
                    <td class="r m">{{ num(row.base_science) }}</td>
                    <td class="r m">{{ num(row.program_bonus) }}</td>
                    <td class="r m">{{ num(row.request_reward) }}</td>
                    <td class="r m" :class="{ 'text-[#ff6b6b]': row.penalty_total > 0 }">−{{ num(row.penalty_total) }}</td>
                    <td class="r m">{{ row.completed_tiles ?? '—' }}</td>
                    <td class="r m" :class="{ 'text-[#ff6b6b]': Number(row.required_missing) > 0 }">{{ row.required_missing ?? '—' }}</td>
                    <td class="r m">{{ row.submission_count }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </div>
      </div>
    </div>
    <TeamDetailDialog :entry="selected" :mine="!!selected && team?.id === selected.team_id" :cards="cardMode ? cardBoard!.cards : undefined" :board-label="cardMode ? cardLabel : null" @close="selected = null" />
  </section>
</template>

<style scoped>
/* rows brighten and shift a hair under the pointer, so scanning a long board stays anchored */
.lb-row { cursor: pointer; transition: background-color .2s ease, transform .2s ease; }
.lb-row:focus-visible { outline: 2px solid #78a6ff; outline-offset: -2px; }
.lb-row:hover { background: rgba(49,94,251,.08); transform: translateX(2px); }
@media (prefers-reduced-motion: reduce) { .lb-row:hover { transform: none; } }
.leaderboard-panel { position: relative; background: linear-gradient(180deg, rgba(49,94,251,.04), transparent 30%); }
.live-strip {
  display: flex; align-items: center; gap: .75rem;
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .72rem; text-transform: uppercase; letter-spacing: .12em;
  color: rgba(255,255,255,.55);
}
.live-strip-dot {
  width: .55rem; height: .55rem; border-radius: 999px; background: rgba(255,255,255,.22);
  box-shadow: 0 0 0 0 rgba(120,166,255,0); transition: background .2s ease, box-shadow .2s ease;
}
.live-strip.is-live .live-strip-dot {
  background: #78a6ff; box-shadow: 0 0 0 0 rgba(120,166,255,.35); animation: live-pulse 2s infinite;
}
.live-strip-text { color: #78a6ff; }
.live-strip-time { margin-left: auto; color: rgba(255,255,255,.38); font-size: .68rem; }
@keyframes live-pulse {
  0% { box-shadow: 0 0 0 0 rgba(120,166,255,.35); }
  70% { box-shadow: 0 0 0 8px rgba(120,166,255,0); }
  100% { box-shadow: 0 0 0 0 rgba(120,166,255,0); }
}
@media (prefers-reduced-motion: reduce) {
  .live-strip.is-live .live-strip-dot { animation: none; }
}
</style>
