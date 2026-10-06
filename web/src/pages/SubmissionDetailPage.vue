<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { supabase } from '../lib/supabase'
import { describeError } from '../lib/errors'
import { SUBMISSION_SELECT, PENDING_STATUSES } from '../lib/data'
import { downloadObject, readObjectText } from '../lib/storage'
import { fmtUtc, num } from '../lib/format'
import { PENALTY_KEYS, WAIT_KEYS, outcomeClass, penaltyTotal, terminationTone, type ScoreReport } from '../lib/report'
import { cardFolderName } from '../lib/projectText'
import { scenarioLabel } from '../lib/scenarioLabels'
import { useAuth } from '../stores/auth'
import { useFlash } from '../stores/flash'
import { useSubmissionWatch } from '../composables/useSubmissionWatch'
import { useWorkerStatus } from '../composables/useWorkerStatus'
import { useQuestFlags } from '../composables/useQuestFlags'
import DashShell from '../components/layout/DashShell.vue'
import StatusPill from '../components/layout/StatusPill.vue'
import ObservedSkyMap from '../components/submissions/ObservedSkyMap.vue'
import ActionTimeline from '../components/submissions/ActionTimeline.vue'
import ReplayViewer from '../components/submissions/ReplayViewer.vue'
import SkeletonRows from '../components/layout/SkeletonRows.vue'

const { t, tf, pick, locale } = useI18n()
const i18n = useI18n()
const route = useRoute()
const flash = useFlash()
const { team, refreshMeCached } = useAuth()
const sub = ref<any | null>(null)
const reports = ref<Record<string, ScoreReport | null>>({})
const loading = ref(true)
const busy = ref(false)
const missing = ref(false)
const id = computed(() => String(route.params.id))
const pending = computed(() => Boolean(sub.value && PENDING_STATUSES.has(sub.value.status)))
const worker = useWorkerStatus()
watch(pending, (p) => { if (p) worker.start(id.value); else worker.stop() }, { immediate: true })
const evaluations = computed(() => ((sub.value?.evaluations ?? []) as any[]).slice().sort((a, b) => Number(a.id) - Number(b.id)))
const watcher = useSubmissionWatch(load, () => pending.value)
/** Per evaluation, the one replay position the decision replay and the observed-sky map both show. */
const replayCursor = ref<Record<string, number | null>>({})
// Seeing a scored replay completes the dashboard quest's last Practice step.
const { remember } = useQuestFlags()
watch(() => sub.value?.status, status => { if (status === 'scored') remember('review', 'practice') })

async function load() {
  const { data, error } = await supabase.from('submissions').select(`${SUBMISSION_SELECT}, profiles(name,nickname)`).eq('id', id.value).maybeSingle()
  if (error || !data) { missing.value = true; return }
  sub.value = data
  for (const ev of evaluations.value) {
    if (ev.status === 'scored' && ev.report_path && !(ev.id in reports.value)) {
      try { reports.value[ev.id] = JSON.parse(await readObjectText('results', ev.report_path)) as ScoreReport }
      catch { reports.value[ev.id] = null }
    }
  }
}

const summaryOf = (ev: any) => (ev.summary ?? {}) as Record<string, any>
const penaltyOf = (ev: any, key: string) => Number(summaryOf(ev).penalties?.[key] ?? reports.value[ev.id]?.score.penalties?.[key as never] ?? 0)
const penaltySum = (ev: any) => ev.penalty_total != null ? Number(ev.penalty_total) : reports.value[ev.id] ? penaltyTotal(reports.value[ev.id]!) : 0
const regionRows = (ev: any) => {
  const r = reports.value[ev.id]
  const done = (r?.completion.flexible_by_region ?? summaryOf(ev).flexible_by_region ?? {}) as Record<string, number>
  const short = (r?.completion.flexible_shortfall ?? summaryOf(ev).flexible_shortfall_by_region ?? {}) as Record<string, number>
  const keys = [...new Set([...Object.keys(done), ...Object.keys(short)])].sort()
  return keys.map(k => ({ region: k, done: Number(done[k] ?? 0), short: Number(short[k] ?? 0) }))
}
const waitRows = (ev: any) => {
  const w = (reports.value[ev.id]?.wait_seconds ?? summaryOf(ev).wait_seconds ?? {}) as Record<string, number>
  return WAIT_KEYS.filter(k => w[k] != null).map(k => ({ key: k, seconds: Number(w[k]) }))
}
const isAgentRun = computed(() => sub.value?.kind === 'agent')
const wallclock = (ev: any) => Number(ev.scenarios?.global_wallclock_seconds ?? 0)
const wallclockPct = (ev: any) => { const b = wallclock(ev); return b > 0 ? Math.min(100, (Number(ev.accounted_wallclock_seconds ?? 0) / b) * 100) : 0 }
const rowClass = (a: any) => outcomeClass(a.outcome, a.action)
const fmtSeconds = (s: number) => s >= 36000 ? `${(s / 3600).toFixed(1)} h` : s >= 600 ? `${(s / 60).toFixed(0)} min` : `${num(s, 0)} s`

async function cancel() {
  busy.value = true
  try {
    const { error } = await supabase.rpc('cancel_submission', { p_id: Number(id.value) })
    if (error) throw error
    flash.success(t('flash.submission_cancelled'))
    await load()
  } catch (e) { flash.error(describeError(e, i18n, ['submit.errors'])) }
  finally { busy.value = false }
}
async function download(path: string, name: string) {
  try { await downloadObject('results', path, `sub-${id.value}-${name}`) }
  catch { flash.error(t('subs.download_failed')) }
}
function evalFileStem(ev: { id: string; scenarios?: { slug: string } | null }): string {
  return cardFolderName(ev.scenarios?.slug, ev.id)
}

onMounted(async () => {
  await refreshMeCached()
  try { await load() } finally { loading.value = false }
  if (team.value) watcher.start(team.value.id)
})
</script>

<template>
  <DashShell :kicker="t('subs.title')" :title="tf('subs.detail_title', { id })">
    <template #title-extra><span v-if="sub?.title" class="text3"> · {{ sub.title }}</span></template>
    <SkeletonRows v-if="loading" :rows="6" :cols="3" :label="t('common.loading')" />
    <div v-else-if="missing || !sub" class="panel"><p class="text2">{{ t('common.not_found') }}</p><p class="mt-5"><router-link class="btn sm" to="/submissions">{{ t('subs.back') }}</router-link></p></div>
    <div v-else class="dash-grid">
      <div>
        <!-- submission header + aggregate score card -->
        <div class="panel">
          <div class="hd">
            <h2 class="flex flex-wrap items-center gap-2"><StatusPill :status="sub.status" testid="sub-status" live /> {{ sub.phases ? pick(sub.phases.name_en, sub.phases.name_zh) : '' }} · {{ t(`kind.${sub.kind}`) }}</h2>
            <span class="m xs">{{ fmtUtc(sub.created_at) }} UTC</span>
          </div>
          <p v-if="pending" class="text2 flex items-center gap-3"><span class="live-dot"></span>{{ t('subs.waiting') }}</p>
          <p v-if="pending" class="m xs text3 mt-2" data-testid="worker-status">
            <template v-if="worker.online.value">{{ tf('subs.worker_online', { s: worker.ageSeconds.value ?? 0 }) }}</template>
            <template v-else>{{ t('subs.worker_offline') }}</template>
            <template v-if="sub.status === 'queued' && worker.position.value != null"> · {{ worker.position.value === 0 ? t('subs.queue_next') : tf('subs.queue_ahead', { n: worker.position.value }) }}</template>
            <template v-else-if="sub.status === 'running'"> · {{ t('subs.queue_running') }}</template>
          </p>
          <div v-if="sub.error" class="errors mt-4"><b>{{ t('subs.error') }}:</b> {{ sub.error }}</div>
          <div v-if="sub.score != null" class="score-card mt-4" data-testid="score-card">
            <div>
              <div class="label">{{ t('subs.score') }}<template v-if="evaluations.length > 1"> · {{ t('subs.mean_of_scenarios') }}</template></div>
              <div class="score-big" :class="Number(sub.score) >= 0 ? 'text-[#315efb]' : 'text-[#ff6b6b]'" data-testid="sub-score">{{ num(sub.score) }}</div>
            </div>
            <dl class="kv">
              <dt>{{ t('subs.base_science') }}</dt><dd class="m">{{ num(sub.base_science) }}</dd>
              <dt>{{ t('subs.program_bonus') }}</dt><dd class="m">+{{ num(sub.program_bonus) }}</dd>
              <dt>{{ t('subs.request_reward') }}</dt><dd class="m">+{{ num(sub.request_reward) }}</dd>
              <template v-if="Number(sub.coverage_bonus ?? 0) !== 0">
                <dt>{{ t('subs.coverage_bonus') }}</dt><dd class="m">+{{ num(sub.coverage_bonus) }}<span v-if="sub.coverage_evenness != null" class="text3 xs"> · {{ t('subs.coverage_evenness') }} {{ num(sub.coverage_evenness, 3) }}</span></dd>
              </template>
              <dt>{{ t('subs.penalties') }}</dt><dd class="m text-[#ff6b6b]">−{{ num(sub.penalty_total) }}</dd>
            </dl>
            <dl class="kv">
              <dt>{{ t('subs.tiles_done') }}</dt><dd class="m">{{ sub.completed_tiles ?? '—' }}</dd>
              <dt>{{ t('subs.required_missing') }}</dt><dd class="m" :class="{ 'text-[#ff6b6b]': Number(sub.required_missing) > 0 }">{{ sub.required_missing ?? '—' }}</dd>
              <dt>{{ t('subs.flexible_shortfall') }}</dt><dd class="m">{{ sub.flexible_shortfall ?? '—' }}</dd>
              <dt>{{ t('subs.termination') }}</dt><dd><span v-for="r in String(sub.termination_reason || '').split(';').filter(Boolean)" :key="r" class="pill mr-1" :class="terminationTone(r)">{{ t(`subs.termination_reason.${r}`) }}</span><span v-if="!sub.termination_reason">—</span></dd>
            </dl>
          </div>
          <span v-else data-testid="sub-score" class="hidden">—</span>
          <dl class="kv mt-6">
            <dt>{{ t('subs.file') }}</dt><dd class="m text-sm">{{ sub.original_filename }}</dd>
            <dt>{{ t('subs.sha') }}</dt><dd class="m xs break-all">{{ sub.sha256 }}</dd>
            <dt>{{ t('subs.by') }}</dt><dd>{{ sub.profiles?.nickname || sub.profiles?.name || '—' }}</dd>
            <template v-if="sub.notes"><dt>{{ t('submit.notes') }}</dt><dd class="whitespace-pre-line">{{ sub.notes }}</dd></template>
          </dl>
          <div v-if="sub.status === 'queued'" class="mt-5"><button type="button" class="btn sm danger" :disabled="busy" @click="cancel">{{ t('subs.cancel') }}</button></div>
        </div>

        <!-- one panel per scenario evaluation -->
        <div v-for="ev in evaluations" :key="ev.id" class="panel mt-8" :data-testid="`evaluation-${ev.scenarios?.slug}`">
          <div class="hd">
            <h2 class="flex flex-wrap items-center gap-2"><StatusPill :status="ev.status" /> {{ t('subs.scenario') }}: <span class="m" :title="ev.scenarios?.slug">{{ ev.scenarios ? scenarioLabel(ev.scenarios.slug, ev.scenarios.name, locale) : '—' }}</span></h2>
            <span class="flex flex-wrap items-center gap-2">
              <span v-if="ev.termination_reason" class="pill" :class="terminationTone(ev.termination_reason)" :title="t(`subs.termination_help.${ev.termination_reason}`)" data-testid="termination-pill">{{ t(`subs.termination_reason.${ev.termination_reason}`) }}</span>
              <span v-if="ev.runtime_seconds" class="m xs">{{ t('subs.runtime') }} {{ num(ev.runtime_seconds, 1) }}s</span>
            </span>
          </div>
          <div v-if="ev.error" class="errors">{{ ev.error }}</div>
          <p v-if="ev.termination_reason" class="text3 text-sm">{{ t(`subs.termination_help.${ev.termination_reason}`) }}</p>

          <template v-if="ev.status === 'scored'">
            <!-- score breakdown -->
            <div class="breakdown mt-5">
              <div class="breakdown-total"><div class="label">{{ t('subs.score') }}</div><div class="m text-3xl" :class="Number(ev.score) >= 0 ? 'text-[#f5f5f5]' : 'text-[#ff6b6b]'">{{ num(ev.score) }}</div></div>
              <div><div class="label">{{ t('subs.base_science') }}</div><div class="m text-xl">{{ num(ev.base_science) }}</div></div>
              <div><div class="label">{{ t('subs.program_bonus') }}</div><div class="m text-xl">+{{ num(ev.program_bonus) }}</div></div>
              <div><div class="label">{{ t('subs.request_reward') }}</div><div class="m text-xl">+{{ num(ev.request_reward) }}</div></div>
              <div v-if="Number(ev.coverage_bonus ?? 0) !== 0 || Number(reports[ev.id]?.score.coverage_bonus ?? 0) !== 0">
                <div class="label">{{ t('subs.coverage_bonus') }}</div>
                <div class="m text-xl">+{{ num(ev.coverage_bonus ?? reports[ev.id]?.score.coverage_bonus) }}<span v-if="(ev.coverage_evenness ?? reports[ev.id]?.score.coverage_evenness) != null" class="text3 text-xs"> · {{ t('subs.coverage_evenness') }} {{ num(ev.coverage_evenness ?? reports[ev.id]?.score.coverage_evenness, 3) }}</span></div>
              </div>
              <div><div class="label">{{ t('subs.penalties') }}</div><div class="m text-xl text-[#ff6b6b]">−{{ num(penaltySum(ev)) }}</div></div>
            </div>
            <div class="table-wrap mt-4">
              <table class="data-table small" data-testid="penalty-table">
                <thead><tr><th v-for="k in PENALTY_KEYS" :key="k" class="r">{{ t(`subs.penalty_kind.${k}`) }}</th></tr></thead>
                <tbody><tr><td v-for="k in PENALTY_KEYS" :key="k" class="r m" :class="penaltyOf(ev, k) > 0 ? 'text-[#ff6b6b]' : 'text3'">{{ penaltyOf(ev, k) > 0 ? '−' : '' }}{{ num(penaltyOf(ev, k), 1) }}</td></tr></tbody>
              </table>
            </div>

            <!-- completion -->
            <div class="grid gap-6 mt-6 md:grid-cols-2">
              <div>
                <h3 class="label">{{ t('subs.completion_title') }}</h3>
                <p class="mt-2 text-2xl font-semibold tracking-[-.03em] text-text-primary"><span class="m">{{ ev.completed_tiles ?? summaryOf(ev).completed_tiles ?? '—' }}</span> <span class="text3 text-base">/ {{ summaryOf(ev).total_tiles || ev.scenarios?.n_tiles || '—'}} {{ t('subs.tiles') }}</span></p>
                <p class="text-sm mt-2"><span class="text3">{{ t('subs.required_missing') }}:</span> <span :class="(reports[ev.id]?.completion.required_missing ?? summaryOf(ev).required_missing_ids ?? []).length ? 'text-[#ff6b6b] m' : 'accent-l'">{{ (reports[ev.id]?.completion.required_missing ?? summaryOf(ev).required_missing_ids ?? []).join(', ') || t('subs.none_missing') }}</span></p>
                <p class="text-sm mt-1"><span class="text3">{{ t('subs.exposures') }}:</span> <span class="m">{{ summaryOf(ev).n_completed_exposures ?? '—' }}</span> · <span class="text3">{{ t('subs.actions') }}:</span> <span class="m">{{ summaryOf(ev).n_actions ?? reports[ev.id]?.actions.length ?? '—' }}</span></p>
              </div>
              <div>
                <h3 class="label">{{ t('subs.flexible_by_region') }}</h3>
                <div class="regions mt-3" data-testid="region-grid">
                  <div v-for="r in regionRows(ev)" :key="r.region" :class="{ 'is-short': r.short > 0 }">{{ r.region }}<div class="bar"><i :style="{ width: `${Math.min(100, (r.done / 4) * 100)}%` }"></i></div>{{ r.done }} / 4<template v-if="r.short > 0"> · −{{ r.short }}</template></div>
                  <div v-if="!regionRows(ev).length" class="text3">—</div>
                </div>
              </div>
            </div>

            <!-- requests + waits -->
            <div class="grid gap-6 mt-6 md:grid-cols-2">
              <div>
                <h3 class="label">{{ t('subs.requests_title') }} <span class="text3">({{ summaryOf(ev).requests_completed ?? '–' }} / {{ summaryOf(ev).requests_total ?? reports[ev.id]?.requests.length ?? '–' }})</span></h3>
                <div v-if="reports[ev.id]?.requests.length" class="table-wrap mt-3">
                  <table class="data-table small" data-testid="request-table">
                    <thead><tr><th>ID</th><th>{{ t('common.status') }}</th><th class="r">{{ t('subs.req_satisfied') }}</th><th class="r">{{ t('subs.req_feasible') }}</th><th class="r">{{ t('subs.req_reward') }}</th><th class="r">{{ t('subs.req_penalty') }}</th></tr></thead>
                    <tbody>
                      <tr v-for="r in reports[ev.id]!.requests" :key="r.request_id">
                        <td class="m">{{ r.request_id }}</td><td><span class="pill" :class="r.status === 'completed' ? 'ok' : r.status === 'excused_unobservable' ? 'warning' : r.status === 'expired' || r.status === 'missed' ? 'failed' : ''">{{ t(`subs.request_status.${r.status}`) === `subs.request_status.${r.status}` ? r.status : t(`subs.request_status.${r.status}`) }}</span></td>
                        <td class="r m">{{ r.satisfied_tile_count }} / {{ r.required_tile_count }}</td><td class="r m">{{ r.feasible_tile_count ?? '—' }}</td>
                        <td class="r m" :class="{ 'accent-l': r.reward > 0 }">{{ r.reward > 0 ? '+' : '' }}{{ num(r.reward, 0) }}</td><td class="r m" :class="{ 'text-[#ff6b6b]': r.penalty > 0 }">{{ r.penalty > 0 ? '−' : '' }}{{ num(r.penalty, 0) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p v-else class="text3 mt-2 text-sm">{{ t('subs.no_requests') }}</p>
              </div>
              <div>
                <h3 class="label">{{ t('subs.wait_title') }}</h3>
                <dl class="kv mt-3" data-testid="wait-seconds">
                  <template v-for="w in waitRows(ev)" :key="w.key"><dt>{{ t(`subs.wait_kind.${w.key}`) }}</dt><dd class="m text-sm" :class="{ 'text-[#f0b64a]': w.key === 'avoidable' && w.seconds > 0 }">{{ fmtSeconds(w.seconds) }}</dd></template>
                  <template v-if="!waitRows(ev).length"><dt>—</dt><dd class="text3">—</dd></template>
                </dl>
              </div>
            </div>

            <!-- agent run panel -->
            <div v-if="isAgentRun" class="agent-panel mt-6" data-testid="agent-panel">
              <h3 class="label">{{ t('subs.agent_run') }}</h3>
              <dl class="kv mt-3">
                <dt>{{ t('subs.committed_actions') }}</dt><dd class="m text-sm">{{ summaryOf(ev).committed_actions ?? summaryOf(ev).n_actions ?? '—' }}<template v-if="summaryOf(ev).ignored_in_flight_response"> · {{ t('subs.in_flight_ignored') }}</template></dd>
                <dt>{{ t('subs.wallclock') }}</dt>
                <dd>
                  <div class="m text-sm">{{ num(ev.accounted_wallclock_seconds, 1) }} s / {{ wallclock(ev) || '—' }} s</div>
                  <div class="wallclock-bar" :title="`${wallclockPct(ev).toFixed(1)}%`"><i :style="{ width: `${wallclockPct(ev)}%` }" :class="{ over: wallclockPct(ev) >= 99.5 }"></i></div>
                </dd>
                <template v-if="summaryOf(ev).agent_error"><dt>{{ t('subs.agent_error') }}</dt><dd class="text-sm text-[#ff6b6b] break-words">{{ summaryOf(ev).agent_error }}</dd></template>
              </dl>
              <details v-if="summaryOf(ev).stderr_tail" class="plain mt-3"><summary class="text-sm">{{ t('subs.stderr') }}</summary><pre class="code-block mt-3">{{ summaryOf(ev).stderr_tail }}</pre></details>
            </div>

            <!-- replay -->
            <div v-if="ev.replay_path" class="mt-8">
              <h3 class="label">{{ t('subs.replay.title') }}</h3>
              <ReplayViewer v-model:cursor="replayCursor[ev.id]" class="mt-3" :path="ev.replay_path" :actions="reports[ev.id]?.actions.length" />
            </div>

            <template v-if="reports[ev.id]">
              <ObservedSkyMap v-if="ev.scenarios?.slug" class="mt-8" :slug="ev.scenarios.slug" :actions="reports[ev.id]!.actions" :tiles-public="ev.scenarios?.tiles_public !== false" v-model:cursor="replayCursor[ev.id]" />
              <h3 class="label mt-8">{{ t('subs.actions_title') }}</h3>
              <ActionTimeline :actions="reports[ev.id]!.actions" />
              <details class="plain mt-6">
                <summary class="label">{{ t('subs.show_all') }} ({{ reports[ev.id]!.actions.length }})</summary>
                <div class="table-wrap mt-3">
                  <table class="data-table small">
                    <thead><tr><th>#</th><th>slot</th><th>UTC</th><th>action</th><th>tile</th><th>program</th><th>request</th><th>outcome</th><th class="r">s</th><th class="r">base</th><th class="r">bonus</th><th class="r">penalty</th></tr></thead>
                    <tbody>
                      <tr v-for="a in reports[ev.id]!.actions" :key="a.decision_id" :class="`row-${rowClass(a)}`">
                        <td class="m">{{ a.decision_id }}</td><td class="m">{{ a.slot_id }}</td><td class="m xs whitespace-nowrap">{{ fmtUtc(a.start_utc, { seconds: true }) }}</td><td>{{ a.action }}</td><td class="m">{{ a.tile_id }}</td><td class="m">{{ a.program }}</td><td class="m">{{ a.request_id }}</td>
                        <td :class="rowClass(a) === 'completed' ? 'accent-l' : rowClass(a) === 'wait' ? 'text3' : 'text-[#ff6b6b]'">{{ a.outcome }}</td>
                        <td class="r m">{{ a.elapsed_seconds }}</td><td class="r m">{{ num(a.base_science_score, 1) }}</td><td class="r m">{{ num(a.program_bonus_score, 1) }}</td><td class="r m" :class="{ 'text-[#ff6b6b]': a.penalty > 0 }">{{ num(a.penalty, 1) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </details>
            </template>
            <p v-else-if="ev.report_path && reports[ev.id] === null" class="text3 mt-4 text-sm">{{ t('subs.no_report') }}</p>
          </template>
          <template v-else-if="isAgentRun && (summaryOf(ev).stderr_tail || summaryOf(ev).agent_error)">
            <p v-if="summaryOf(ev).agent_error" class="mt-3 text-sm text-[#ff6b6b]">{{ summaryOf(ev).agent_error }}</p>
            <details v-if="summaryOf(ev).stderr_tail" class="plain mt-3"><summary class="text-sm">{{ t('subs.stderr') }}</summary><pre class="code-block mt-3">{{ summaryOf(ev).stderr_tail }}</pre></details>
          </template>

          <p class="actions-inline mt-5">
            <button v-if="ev.report_path" type="button" class="btn sm" @click="download(ev.report_path, `${evalFileStem(ev)}-score_report.json`)">{{ t('subs.report') }} ↓</button>
            <button v-if="ev.decisions_path" type="button" class="btn sm" @click="download(ev.decisions_path, `${evalFileStem(ev)}-decisions.csv`)">{{ t('subs.decisions') }} ↓</button>
            <button v-if="ev.log_path" type="button" class="btn sm" @click="download(ev.log_path, `${evalFileStem(ev)}-agent.log`)">{{ t('subs.log') }} ↓</button>
            <button v-if="ev.workflow_path" type="button" class="btn sm" @click="download(ev.workflow_path, `${evalFileStem(ev)}-workflow_result.json`)">{{ t('subs.workflow') }} ↓</button>
            <button v-if="ev.replay_path" type="button" class="btn sm" @click="download(ev.replay_path, `${evalFileStem(ev)}-decision_replay.html`)">{{ t('subs.replay.download') }} ↓</button>
          </p>
        </div>
      </div>
      <div>
        <div class="panel">
          <div class="hd"><h2>{{ t('subs.reading_title') }}</h2></div>
          <p class="text2 text-sm">{{ t('subs.reading_formula') }}</p>
          <pre class="code-block mt-3 text-xs">total = base_science + program_bonus + request_reward + coverage_bonus
      − unsafe_observation − invalid_action − avoidable_wait
      − required_miss − flexible_shortfall − request_miss

coverage_bonus = coverage_bonus_weight × base_science × coverage_evenness</pre>
          <p class="text3 mt-3 text-xs">{{ t('subs.coverage_weight_note') }}</p>
          <p class="text3 mt-3 text-xs">{{ t('subs.reading_note') }}</p>
          <p class="mt-5"><router-link class="label accent" to="/rules">{{ t('home.evaluation.link') }} →</router-link></p>
        </div>
        <div class="panel mt-8">
          <div class="hd"><h2>{{ t('leaderboard.title') }}</h2></div>
          <p class="text2 text-sm">{{ t('leaderboard.tie') }}</p>
          <p class="mt-5 actions-inline">
            <router-link class="btn sm" :to="sub.phases ? `/leaderboard/${sub.phases.slug}` : '/leaderboard'">{{ t('leaderboard.full') }} →</router-link>
            <router-link class="btn sm" to="/submissions">{{ t('subs.back') }}</router-link>
          </p>
        </div>
      </div>
    </div>
  </DashShell>
</template>

<style scoped>
.score-card { display: grid; gap: 1.5rem 2.5rem; align-items: start; }
@media (min-width: 900px) { .score-card { grid-template-columns: auto 1fr 1fr; } }
.breakdown { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1px; border: 1px solid #2a2a2a; background: #2a2a2a; }
.breakdown > div { background: #0b0b0b; padding: .8rem 1rem; min-width: 0; }
.breakdown-total { grid-column: span 2; }
@media (min-width: 720px) { .breakdown { grid-template-columns: 1.4fr repeat(4, 1fr); } .breakdown-total { grid-column: auto; } }
.regions > div.is-short { color: #f0b64a; }
.regions .bar { height: 4px; background: rgba(255,255,255,.08); }
.regions .bar i { display: block; height: 100%; background: #315efb; }
.regions > div.is-short .bar i { background: #f0b64a; }
.agent-panel { border: 1px solid #2a2a2a; padding: 1rem 1.25rem; background: rgba(255,255,255,.02); }
.wallclock-bar { position: relative; height: 5px; width: 100%; max-width: 20rem; margin-top: .35rem; background: rgba(255,255,255,.08); }
.wallclock-bar i { position: absolute; left: 0; top: 0; bottom: 0; background: #315efb; }
.wallclock-bar i.over { background: #f0b64a; }
tr.row-unsafe td, tr.row-invalid td { background: rgba(122,42,42,.12); }
tr.row-interrupted td { background: rgba(184,134,11,.08); }
</style>
