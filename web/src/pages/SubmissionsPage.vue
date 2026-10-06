<script setup lang="ts">
import { competition } from '../stores/competition'
import EvaluationHistory from '../components/dashboard/EvaluationHistory.vue'
import { onMounted, ref } from 'vue'
import { useI18n } from '../composables/useI18n'
import { supabase } from '../lib/supabase'
import { SUBMISSION_SELECT, PENDING_STATUSES } from '../lib/data'
import { scenarioLabel } from '../lib/scenarioLabels'
import { fmtUtc, num } from '../lib/format'
import { useAuth } from '../stores/auth'
import { useSubmissionWatch } from '../composables/useSubmissionWatch'
import DashShell from '../components/layout/DashShell.vue'
import StatusPill from '../components/layout/StatusPill.vue'
import SkeletonRows from '../components/layout/SkeletonRows.vue'

const { t, pick, locale } = useI18n()
const { team, refreshMeCached } = useAuth()
const rows = ref<any[]>([])
const loading = ref(true)
const watcher = useSubmissionWatch(load, () => rows.value.some(r => PENDING_STATUSES.has(r.status)))

async function load() {
  if (!team.value) { rows.value = []; return }
  const { data } = await supabase.from('submissions').select(`${SUBMISSION_SELECT}, profiles(name,nickname)`).eq('team_id', team.value.id).order('created_at', { ascending: false }).limit(200)
  rows.value = data ?? []
}

onMounted(async () => {
  await refreshMeCached()
  try { await load() } finally { loading.value = false }
  if (team.value) watcher.start(team.value.id)
})
</script>

<template>
  <DashShell :kicker="t('dash.title')" :title="t('subs.title')">
    <!-- Complete-project evaluations live in observer_batches, not in CSV submissions. -->
    <EvaluationHistory v-if="team" all-phases :hide-empty="competition.mode!=='competition'" />
    <details :open="competition.mode==='practice'">
      <summary v-if="competition.mode==='competition'" class="btn sm mb-5">{{ pick('Earlier submissions','查看已有提交') }}</summary>
    <SkeletonRows v-if="loading" :rows="6" :cols="6" :label="t('common.loading')" />
    <div v-else-if="!team" class="panel"><p class="text2">{{ t('submit.errors.need_team') }}</p><p class="mt-5"><router-link class="btn primary sm" to="/team">{{ t('nav.team') }} →</router-link></p></div>
    <p v-else-if="!rows.length" class="text2">{{ t('subs.empty_csv') }} <router-link class="accent-l" to="/compete">{{ t('dash.new_submission') }} →</router-link></p>
    <div v-else class="table-wrap">
      <table class="data-table">
        <thead><tr><th>{{ t('subs.id') }}</th><th>{{ t('subs.when') }}</th><th>{{ t('subs.phase') }}</th><th>{{ t('subs.kind') }}</th><th>{{ t('subs.scenario') }}</th><th>{{ t('common.status') }}</th><th class="r">{{ t('subs.score') }}</th><th class="r">{{ t('subs.base_science') }}</th><th class="r">{{ t('subs.penalties') }}</th><th class="r">{{ t('subs.tiles_done') }}</th><th>{{ t('subs.termination') }}</th><th>{{ t('subs.by') }}</th></tr></thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
            <td><router-link class="accent-l m" :to="`/submissions/${s.id}`">#{{ s.id }}</router-link><div v-if="s.title" class="text3 text-xs">{{ s.title }}</div></td>
            <td class="m xs whitespace-nowrap">{{ fmtUtc(s.created_at) }}</td>
            <td>{{ s.phases ? pick(s.phases.name_en, s.phases.name_zh) : '—' }}</td>
            <td>{{ t(`kind.${s.kind}`) }}</td>
            <td class="m xs">{{ s.scenarios ? scenarioLabel(s.scenarios.slug, s.scenarios.name, locale) : '—' }}</td>
            <td><StatusPill :status="s.status" /></td>
            <td class="r m" :class="{ 'text-[#ff6b6b]': Number(s.score) < 0 }">{{ num(s.score) }}</td>
            <td class="r m">{{ num(s.base_science) }}</td>
            <td class="r m" :class="{ 'text-[#ff6b6b]': Number(s.penalty_total) > 0 }">{{ s.penalty_total != null ? '−' + num(s.penalty_total) : '—' }}</td>
            <td class="r m">{{ s.completed_tiles ?? '—' }}</td>
            <td class="xs">{{ s.termination_reason ? String(s.termination_reason).split(';').map((r: string) => t(`subs.termination_reason.${r}`)).join(' · ') : '—' }}</td>
            <td class="text-sm">{{ s.profiles?.nickname || s.profiles?.name || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    </details>
  </DashShell>
</template>
