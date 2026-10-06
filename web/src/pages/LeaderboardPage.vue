<script setup lang="ts">
import UserAvatar from '../components/UserAvatar.vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { usePhases } from '../composables/usePhases'
import { boardScenarios, isFinalBoard, isProjectBoard, isPublicFormalBoard, loadCardBoard, loadLeaderboard, phaseCopy, type CardBoard, type LeaderboardEntry, type Phase } from '../lib/data'
import { LEADERBOARD_PAGE_SLUGS, LEADERBOARD_SLUGS, LEADERBOARD_TAB_LABEL_KEYS } from '../lib/leaderboardBoards'
import { scenarioLabel, scenarioOrder } from '../lib/scenarioLabels'
import { useAuth } from '../stores/auth'
import { fmtUtc, num } from '../lib/format'
import PageHead from '../components/layout/PageHead.vue'
import ScoreBars from '../components/leaderboard/ScoreBars.vue'
import SkeletonRows from '../components/layout/SkeletonRows.vue'
import BoardScenarioTabs from '../components/leaderboard/BoardScenarioTabs.vue'
import TeamDetailDialog from '../components/leaderboard/TeamDetailDialog.vue'
import BoardCardTabs from '../components/leaderboard/BoardCardTabs.vue'
import CardBoardTable from '../components/leaderboard/CardBoardTable.vue'

const { t, tf, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const { team, isAdmin } = useAuth()
const { phases, loading: phasesLoading, reload } = usePhases(false)
const entries = ref<LeaderboardEntry[]>([])
const boardLoading = ref(false)
const updatedAt = ref<Date | null>(null)
const selected = ref<LeaderboardEntry | null>(null)
let timer: number | undefined

// The hidden final's tab comes last and exists only for phases the database returns (participants: once published).
// Organizers may also open any phase they can read at /leaderboard/<slug> (e.g. a rehearsal) as a preview tab.
const visiblePhases = computed(() => {
  const listed = LEADERBOARD_PAGE_SLUGS.map(slug => phases.value.find(p => p.slug === slug)).filter((p): p is Phase => !!p)
  const slug = route.params.phase as string | undefined
  const extra = isAdmin.value && slug && !listed.some(p => p.slug === slug) ? phases.value.find(p => p.slug === slug) : undefined
  return extra ? [...listed, extra] : listed
})
// Without a slug the default stays among the original three boards, so the final never displaces them.
const defaultPhases = computed(() => visiblePhases.value.filter(p => (LEADERBOARD_SLUGS as readonly string[]).includes(p.slug)))
const phase = computed<Phase | null>(() => {
  const slug = route.params.phase as string | undefined
  if (slug) return visiblePhases.value.find(p => p.slug === slug) ?? null
  return defaultPhases.value.find(p => p.counts_for_final && (p.status === 'open' || p.status === 'closed')) ?? defaultPhases.value.find(p => p.status === 'open') ?? defaultPhases.value[0] ?? null
})
const tabLabel = (p: Phase) => LEADERBOARD_TAB_LABEL_KEYS[p.slug] ? t(LEADERBOARD_TAB_LABEL_KEYS[p.slug]!) : (locale.value === 'zh' ? p.name_zh : p.name_en) || p.slug
// One short plain line replaces all status badges: no extra wording beyond these two cases.
const statusLine = computed(() => {
  if (!phase.value) return null
  if (phase.value.slug === 'practice') return t('leaderboard.debug_submissions_closed')
  if (phase.value.slug === 'online' && phase.value.status === 'upcoming') return t('leaderboard.competition_starts')
  return null
})
// Organizers see an unpublished board too, marked as a preview; the database decides what anyone else gets.
const preview = computed(() => !!isAdmin.value && phase.value?.leaderboard_mode === 'hidden')
const visible = computed(() => phase.value != null && (phase.value.leaderboard_mode !== 'hidden' || preview.value))
// Practice boards rank one scenario at a time (?scenario=…); the final board averages every scenario.
const scenarioTabs = computed(() => boardScenarios(phase.value))
const sortedScenarios = computed(() => [...(phase.value?.scenarios ?? [])].sort((a, b) => scenarioOrder(a.slug) - scenarioOrder(b.slug)))
const scenarioSlug = computed<string | null>(() => {
  const wanted = route.query.scenario
  return scenarioTabs.value.find(s => s.slug === wanted)?.slug ?? scenarioTabs.value[0]?.slug ?? null
})
function pickScenario(slug: string) { void router.replace({ query: { ...route.query, scenario: slug } }) }
// Complete-project phases whose board_layout is 'cards' / 'cards_overall' rank per card (?scenario=<card>) and overall.
const cardBoard = ref<CardBoard | null>(null)
const cardMode = computed(() => !!cardBoard.value && cardBoard.value.layout !== 'overall' && cardBoard.value.cards.length > 0)
const cardTab = computed(() => cardBoard.value?.scenario ?? null)
const cardLabel = computed(() => cardTab.value === null ? t('leaderboard.detail.board_overall')
  : tf('leaderboard.detail.board_card', { card: scenarioLabel(cardTab.value, cardBoard.value?.cards.find(c => c.slug === cardTab.value)?.name ?? cardTab.value, locale.value) }))
function pickCard(slug: string | null) {
  const { scenario: _drop, ...rest } = route.query
  void router.replace({ query: slug === null ? rest : { ...rest, scenario: slug } })
}
// The coverage term only exists where the scenario's score_config sets a weight, so keep the column out of practice phases.
const showCoverage = computed(() => entries.value.some(e => (e.coverage_bonus ?? 0) !== 0))

async function loadBoard() {
  if (!phase.value || !visible.value) { entries.value = []; return }
  boardLoading.value = true
  try {
    if (isProjectBoard(phase.value)) {
      const wanted = typeof route.query.scenario === 'string' ? route.query.scenario : null
      cardBoard.value = await loadCardBoard(phase.value.id, wanted)
      entries.value = cardBoard.value.rows
    } else {
      cardBoard.value = null
      entries.value = await loadLeaderboard(phase.value.slug, 500, scenarioSlug.value)
    }
    updatedAt.value = new Date()
  }
  catch { entries.value = []; cardBoard.value = null }
  finally { boardLoading.value = false }
}

watch(() => [phase.value?.slug, scenarioSlug.value, route.query.scenario, visible.value], () => { void loadBoard() })
function pollBoard() { if (phase.value?.leaderboard_mode === 'live' && document.visibilityState === 'visible') void loadBoard() }
onMounted(async () => {
  await reload()
  await loadBoard()
  timer = window.setInterval(pollBoard, 60_000)
  document.addEventListener('visibilitychange', pollBoard)
})
onUnmounted(() => { if (timer) window.clearInterval(timer); document.removeEventListener('visibilitychange', pollBoard) })
</script>

<template>
  <main class="poster-canvas">
    <PageHead :kicker="t('leaderboard.kicker')" :title="t('leaderboard.title')" :lede="t('leaderboard.intro')" />
    <section class="section tight"><div class="wrap">
      <div v-if="visiblePhases.length" class="tabs">
        <router-link v-for="p in visiblePhases" :key="p.id" :to="`/leaderboard/${p.slug}`" :class="{ active: phase && p.id === phase.id }" :data-testid="`board-tab-${p.slug}`">{{ tabLabel(p) }}</router-link>
      </div>
      <p v-if="statusLine" class="text3 mt-2 text-sm">{{ statusLine }}</p>

      <p v-if="phasesLoading" class="text3 mt-8 text-sm">{{ t('common.loading') }}</p>
      <p v-else-if="!phase" class="text2 mt-8">{{ t('leaderboard.no_phases') }}</p>

      <div v-else class="mt-12 grid gap-12 lg:grid-cols-[.52fr_1.48fr] lg:gap-14">
        <div class="lg:sticky lg:top-28 lg:self-start">
          <p class="text2">{{ phaseCopy(phase, locale).description }}</p>
          <p class="text3 mt-3 text-sm">{{ phaseCopy(phase, locale).facts.join(' · ') }}</p>
          <dl class="kv mt-8">
            <template v-if="phase.starts_at || phase.ends_at"><dt>{{ t('common.utc') }}</dt><dd class="m text-sm">{{ fmtUtc(phase.starts_at) }} → {{ fmtUtc(phase.ends_at) }}</dd></template>
            <dt>{{ t('leaderboard.scenarios') }}</dt>
            <dd v-if="cardMode" class="flex flex-wrap gap-2"><span v-for="c in cardBoard!.cards" :key="c.slug" class="pill">{{ scenarioLabel(c.slug, c.name, locale) }}</span></dd>
            <dd v-else class="flex flex-wrap gap-2"><span v-for="s in sortedScenarios" :key="s.id" class="pill" :title="s.slug">{{ scenarioLabel(s.slug, s.name, locale) }} · {{ s.n_nights ?? '?' }}n · {{ s.global_wallclock_seconds ?? '?' }}s<template v-if="!s.weather_public"> · {{ t('common.hidden') }}</template></span><span v-if="!phase.scenarios.length" class="text3">—</span></dd>
            <dt>{{ t('common.updated') }}</dt><dd class="m text-sm">{{ updatedAt ? fmtUtc(updatedAt.toISOString(), { seconds: true }) : '—' }} UTC</dd>
          </dl>
          <p class="text3 mt-8 text-sm">{{ t('leaderboard.tie') }} <template v-if="cardMode">{{ cardTab === null ? t('leaderboard.overall_note') : t('leaderboard.card_note') }}</template><template v-else-if="scenarioTabs.length">{{ t('leaderboard.per_scenario_note') }}</template><template v-else-if="phase.scenarios.length > 1">{{ t('leaderboard.mean_note') }}</template></p>
          <p class="mt-6"><button type="button" class="btn sm" :disabled="boardLoading" @click="loadBoard">↻ {{ t('leaderboard.refresh') }}</button></p>
        </div>
        <div class="min-w-0">
          <p v-if="preview" class="notice mb-6" data-testid="board-preview-note">{{ t('leaderboard.organizer_preview') }}</p>
          <p v-if="isPublicFormalBoard(phase)" class="notice mb-6" data-testid="board-public-note">{{ t('leaderboard.public_board') }}</p>
          <p v-else-if="isFinalBoard(phase)" class="notice mb-6" data-testid="board-final-note">{{ t('leaderboard.final_board') }}</p>
          <BoardCardTabs v-if="visible && cardMode" class="mb-6" :layout="cardBoard!.layout" :cards="cardBoard!.cards" :model-value="cardTab" @update:model-value="pickCard" />
          <BoardScenarioTabs v-if="visible && !cardMode" class="mb-6" :scenarios="scenarioTabs" :model-value="scenarioSlug" @update:model-value="pickScenario" />
          <p v-if="!visible" class="text2 py-12">{{ t('leaderboard.hidden') }}</p>
          <SkeletonRows v-else-if="boardLoading && !entries.length" :rows="8" :cols="6" :label="t('common.loading')" />
          <div v-else-if="!entries.length" class="py-16 text-center">
            <div class="empty-zero">00</div>
            <p class="text2 mt-3 text-sm">{{ t('leaderboard.empty') }}</p>
          </div>
          <template v-else>
            <p class="label mb-4">{{ tf('leaderboard.n_entries', { n: entries.length }) }}</p>
            <CardBoardTable v-if="cardMode" :entries="entries" :layout="cardBoard!.layout" :cards="cardBoard!.cards" :tab="cardTab" :team-id="team?.id ?? null" @select="selected = $event" />
            <template v-else>
            <ScoreBars class="mb-8" :entries="entries" :team-id="team?.id ?? null" :updated-at="updatedAt" @select="selected = $event" />
            <div class="table-wrap">
              <table class="data-table">
                <thead><tr><th>{{ t('leaderboard.rank') }}</th><th>{{ t('leaderboard.team') }}</th><th class="r">{{ t('leaderboard.score') }}</th><th class="r">{{ t('leaderboard.base_science') }}</th><th class="r">{{ t('leaderboard.bonus') }}</th><th class="r">{{ t('leaderboard.requests') }}</th><th v-if="showCoverage" class="r">{{ t('leaderboard.coverage') }}</th><th class="r">{{ t('leaderboard.penalties') }}</th><th class="r">{{ t('leaderboard.tiles') }}</th><th class="r">{{ t('leaderboard.required_missing') }}</th><th class="r">{{ t('leaderboard.submissions') }}</th></tr></thead>
                <tbody>
                  <tr v-for="row in entries" :key="row.team_id" data-testid="lb-row" class="lb-click" :class="{ me: team && team.id === row.team_id }" tabindex="0" @click="selected = row" @keydown.enter.prevent="selected = row">
                    <td class="m rank-cell" :class="row.rank <= 3 ? `rank-${row.rank}` : ''">{{ row.rank }}</td>
                    <td><span class="team-cell"><UserAvatar :name="row.team_name" :github="row.leader_github" :avatar-url="row.leader_avatar_url" /><i v-if="row.rank === 1" class="champ-star" aria-hidden="true">✦</i><span class="team-name">{{ row.team_name }}</span></span><span v-if="team && team.id === row.team_id" class="label accent ml-2">{{ t('leaderboard.me') }}</span></td>
                    <td class="r m" :class="{ 'text-[#ff6b6b]': row.total_score < 0 }">{{ num(row.total_score) }}</td>
                    <td class="r m">{{ num(row.base_science) }}</td>
                    <td class="r m">{{ num(row.program_bonus) }}</td>
                    <td class="r m">{{ num(row.request_reward) }}</td>
                    <td v-if="showCoverage" class="r m">{{ row.coverage_bonus == null ? '—' : `+${num(row.coverage_bonus)}` }}</td>
                    <td class="r m" :class="{ 'text-[#ff6b6b]': row.penalty_total > 0 }">−{{ num(row.penalty_total) }}</td>
                    <td class="r m">{{ row.completed_tiles ?? '—' }}</td>
                    <td class="r m" :class="{ 'text-[#ff6b6b]': Number(row.required_missing) > 0 }">{{ row.required_missing ?? '—' }}</td>
                    <td class="r m">{{ row.submission_count }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            </template>
          </template>
        </div>
      </div>
    </div></section>
    <TeamDetailDialog :entry="selected" :mine="!!selected && team?.id === selected.team_id" :cards="cardMode ? cardBoard!.cards : undefined" :board-label="cardMode ? cardLabel : null" @close="selected = null" />
  </main>
</template>

<style scoped>
.lb-click { cursor: pointer; transition: background-color .2s ease; }
.lb-click:hover { background: rgba(49,94,251,.08); }
.lb-click:focus-visible { outline: 2px solid #78a6ff; outline-offset: -2px; }
</style>
