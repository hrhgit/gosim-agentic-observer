<script setup lang="ts">
import EvaluationHistory from '../components/dashboard/EvaluationHistory.vue'
import { competition } from '../stores/competition'
import UserAvatar from '../components/UserAvatar.vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { supabase } from '../lib/supabase'
import { loadPhases, SUBMISSION_SELECT, PENDING_STATUSES, type Phase } from '../lib/data'
import { fmtUtc, num } from '../lib/format'
import { useAuth } from '../stores/auth'
import { useFlash } from '../stores/flash'
import { useSubmissionWatch } from '../composables/useSubmissionWatch'
import DashShell from '../components/layout/DashShell.vue'
import StatusPill from '../components/layout/StatusPill.vue'
import SkeletonRows from '../components/layout/SkeletonRows.vue'
import KimiPlanPanel from '../components/dashboard/KimiPlanPanel.vue'
import QuestPanel from '../components/dashboard/QuestPanel.vue'
import TeamInbox from '../components/TeamInbox.vue'
import { useQuestFlags } from '../composables/useQuestFlags'
import { questProgress } from '../lib/quest'

const { t, tf, pick } = useI18n()
const route = useRoute()
const flash = useFlash()
const { me, team, refreshMeCached } = useAuth()
const phases = ref<Phase[]>([])
const quota = ref<Record<string, number>>({})
const submissions = ref<any[]>([])
const members = ref<any[]>([])
const loading = ref(true)
// Formal-competition progress for the quest: the latest evaluation batch and whether a project exists.
const latestBatch = ref<string | null>(null)
const projectCount = ref(0)
const { flags, remember } = useQuestFlags()
const quest = computed(() => questProgress({
  hasTeam: Boolean(team.value),
  prepared: Boolean(flags.value.prepare) || (competition.mode === 'competition' && projectCount.value > 0),
  submitted: competition.mode === 'competition' ? Boolean(latestBatch.value) : submissions.value.length > 0,
  reviewed: Boolean(flags.value.review),
}))
const questResultTo = computed(() => {
  if (competition.mode === 'competition') return latestBatch.value ? `/compete#batch-${latestBatch.value}` : '/submissions'
  const shown = submissions.value.find(s => s.status === 'scored') ?? submissions.value[0]
  return shown ? `/submissions/${shown.id}` : '/submissions'
})
const watcher = useSubmissionWatch(loadSubmissions, () => submissions.value.some(s => PENDING_STATUSES.has(s.status)))

async function loadFormalProgress() {
  if (!team.value) return
  const [batches, projects] = await Promise.all([
    supabase.from('observer_batches').select('id').eq('team_id', team.value.id).eq('purpose', 'formal').order('created_at', { ascending: false }).limit(1),
    supabase.from('observer_projects').select('id', { count: 'exact', head: true }).eq('team_id', team.value.id),
  ])
  latestBatch.value = batches.data?.[0]?.id ?? null
  projectCount.value = projects.count ?? 0
}

async function loadSubmissions() {
  if (!team.value) return
  const { data } = await supabase.from('submissions').select(SUBMISSION_SELECT).eq('team_id', team.value.id).order('created_at', { ascending: false }).limit(5)
  submissions.value = data ?? []
}

onMounted(async () => {
  if (route.query.denied) flash.error(t('errors.admin_required'))
  await refreshMeCached()
  try {
    phases.value = await loadPhases()
    if (team.value) {
      const formal = competition.mode === 'competition' ? loadFormalProgress().catch(() => undefined) : Promise.resolve()
      await loadSubmissions()
      const [{ data: memberRows }, ...counts] = await Promise.all([
        supabase.rpc('team_members', { p_team_id: team.value.id }),
        ...phases.value.filter(p => p.status === 'open').map(async p => {
          if(competition.mode==='competition') {
            const r=await supabase.from('observer_batches').select('id',{count:'exact',head:true})
              .eq('team_id',team.value!.id).eq('phase_id',p.id).eq('purpose','formal')
              .gte('created_at',new Date().toISOString().slice(0,10)+'T00:00:00Z')
            return [p.slug,r.count??0] as const
          }
          const r=await supabase.rpc('team_daily_count',{p_phase_slug:p.slug});return [p.slug,Number(r.data??0)] as const
        }),
      ])
      members.value = memberRows ?? []
      quota.value = Object.fromEntries(counts)
      await formal
      watcher.start(team.value.id)
    }
  } finally { loading.value = false }
})
</script>

<template>
  <DashShell :kicker="t('dash.title')" :title="tf('dash.welcome', { name: me?.nickname || me?.name || me?.email || '' })">
    <TeamInbox compact />
    <div v-if="loading" class="dash-grid"><div class="panel"><SkeletonRows :rows="5" :cols="5" :label="t('dash.loading')" /></div><div class="panel"><SkeletonRows :rows="3" :cols="2" :label="t('dash.loading')" /></div></div>
    <div v-else class="dash-grid">
      <div>
        <QuestPanel class="mb-8" :mode="competition.mode" :progress="quest" :result-to="questResultTo" />
        <EvaluationHistory v-if="team && competition.mode==='competition'" :limit="5" :quiet="!quest.finished" />
        <div v-else-if="team" class="panel">
          <div class="hd"><h2>{{ t('dash.recent') }}</h2><router-link class="label accent" to="/submissions">{{ t('dash.all_submissions') }} →</router-link></div>
          <div v-if="submissions.length" class="table-wrap">
            <table class="data-table">
              <thead><tr><th>{{ t('subs.id') }}</th><th>{{ t('subs.phase') }}</th><th>{{ t('subs.kind') }}</th><th>{{ t('common.status') }}</th><th class="r">{{ t('subs.score') }}</th><th>{{ t('subs.when') }}</th></tr></thead>
              <tbody>
                <tr v-for="s in submissions" :key="s.id">
                  <td><router-link class="accent-l m" :to="`/submissions/${s.id}`">#{{ s.id }}</router-link></td>
                  <td>{{ s.phases ? pick(s.phases.name_en, s.phases.name_zh) : '—' }}</td>
                  <td>{{ t(`kind.${s.kind}`) }}</td>
                  <td><StatusPill :status="s.status" /></td>
                  <td class="r m">{{ num(s.score) }}</td>
                  <td class="m xs">{{ fmtUtc(s.created_at, { short: true }) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="text2">{{ t('subs.empty') }}</p>
          <p class="mt-5"><router-link class="btn sm" :class="{ primary: quest.finished }" to="/compete">{{ t('dash.new_submission') }} →</router-link></p>
        </div>
      </div>

      <div>
        <div v-if="team" class="panel">
          <div class="hd"><h2>{{ t('dash.team') }}</h2><router-link class="label accent" to="/team">{{ t('nav.team') }} →</router-link></div>
          <p class="text-2xl font-semibold tracking-[-.03em] text-text-primary">{{ team.name }}</p>
          <dl class="kv mt-4">
            <dt>{{ t('dash.members') }}</dt><dd>{{ team.member_count }} / {{ team.max_size }}</dd>
            <dt>{{ t('team.invite_code') }}</dt><dd class="m">{{ team.invite_code }}</dd>
          </dl>
          <ul class="text2 mt-4 text-sm">
            <li v-for="m in members" :key="m.id"><UserAvatar :name="m.name" :github="m.github" :avatar-url="m.avatar_url" /> {{ m.name }}<template v-if="m.is_leader"> · <span class="label accent">{{ t('team.leader') }}</span></template></li>
          </ul>
        </div>
        <KimiPlanPanel class="mt-8" />
        <div class="panel mt-8">
          <div class="hd"><h2>{{ t('resources.kicker') }}</h2></div>
          <div class="flex flex-col gap-2">
            <router-link class="btn sm" to="/start">{{ t('nav.start') }} →</router-link>
            <router-link class="btn sm" to="/resources" @click="remember('prepare')">{{ t('dash.quick.kit') }} →</router-link>
            <router-link class="btn sm" to="/docs">{{ t('dash.quick.docs') }} →</router-link>
          </div>
        </div>
      </div>
    </div>
  </DashShell>
</template>
