<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { supabase } from '../../lib/supabase'
import { loadPhases, loadScenarios, type Phase, type Scenario } from '../../lib/data'
import { toLocalInput, fromLocalInput } from '../../lib/format'
import { useAdmin } from '../../composables/useAdmin'
import DashShell from '../../components/layout/DashShell.vue'
import StatusPill from '../../components/layout/StatusPill.vue'

interface PhaseForm {
  id: string | null; slug: string; sort_order: number; name_en: string; name_zh: string; description_en: string; description_zh: string
  starts_at: string; ends_at: string; daily_limit: number; leaderboard_mode: string; scenario_ids: string[]
  allow_results: boolean; allow_agents: boolean; counts_for_final: boolean; is_active: boolean; status?: string
}
const MODES = ['live', 'frozen', 'hidden', 'published']
const { t, tf, busy, rpc, run, flash } = useAdmin()
const forms = ref<PhaseForm[]>([])
const fresh = ref<PhaseForm>(blank())
const scenarios = ref<Scenario[]>([])

function blank(): PhaseForm {
  return { id: null, slug: '', sort_order: 0, name_en: '', name_zh: '', description_en: '', description_zh: '', starts_at: '', ends_at: '', daily_limit: 10, leaderboard_mode: 'live', scenario_ids: [], allow_results: true, allow_agents: true, counts_for_final: false, is_active: true }
}
function toForm(p: Phase): PhaseForm {
  return { id: p.id, slug: p.slug, sort_order: p.sort_order ?? 0, name_en: p.name_en ?? '', name_zh: p.name_zh ?? '', description_en: p.description_en ?? '', description_zh: p.description_zh ?? '', starts_at: toLocalInput(p.starts_at), ends_at: toLocalInput(p.ends_at), daily_limit: p.daily_limit ?? 10, leaderboard_mode: p.leaderboard_mode, scenario_ids: p.scenarios.map(s => s.id), allow_results: p.allow_results, allow_agents: p.allow_agents, counts_for_final: p.counts_for_final, is_active: p.is_active, status: p.status }
}
async function load() {
  const [phases, scn] = await Promise.all([loadPhases(true), loadScenarios()])
  forms.value = phases.map(toForm)
  scenarios.value = scn
}
async function save(form: PhaseForm) {
  const ok = await run(async () => {
    const payload = {
      slug: form.slug.trim(), sort_order: Number(form.sort_order) || 0, name_en: form.name_en.trim(), name_zh: form.name_zh.trim(),
      description_en: form.description_en, description_zh: form.description_zh, starts_at: fromLocalInput(form.starts_at), ends_at: fromLocalInput(form.ends_at),
      daily_limit: Number(form.daily_limit) || 0, leaderboard_mode: form.leaderboard_mode, allow_results: form.allow_results, allow_agents: form.allow_agents,
      counts_for_final: form.counts_for_final, is_active: form.is_active,
    }
    let id = form.id
    if (id) {
      const { error } = await supabase.from('phases').update(payload).eq('id', id)
      if (error) throw error
    } else {
      const { data, error } = await supabase.from('phases').insert(payload).select('id').single()
      if (error) throw error
      id = (data as { id: string }).id
    }
    const { error: delError } = await supabase.from('phase_scenarios').delete().eq('phase_id', id)
    if (delError) throw delError
    if (form.scenario_ids.length) {
      const { error: insError } = await supabase.from('phase_scenarios').insert(form.scenario_ids.map(scenario_id => ({ phase_id: id, scenario_id })))
      if (insError) throw insError
    }
  }, t('admin.phases.saved'))
  if (ok) { fresh.value = blank(); await load() }
}
async function remove(form: PhaseForm) {
  if (!form.id || !window.confirm(t('admin.phases.delete_confirm'))) return
  const ok = await run(async () => { const { error } = await supabase.from('phases').delete().eq('id', form.id!); if (error) throw error }, t('common.deleted'))
  if (ok) await load()
}
async function rescore(form: PhaseForm) {
  if (!window.confirm(t('admin.phases.rescore_confirm'))) return
  let count = 0
  const ok = await run(async () => { count = Number(await rpc<number>('admin_rescore_phase', { p_phase_slug: form.slug }) ?? 0) })
  if (ok) flash.success(tf('admin.phases.rescored', { n: count }))
}
onMounted(load)
</script>

<template>
  <DashShell admin :kicker="t('admin.kicker')" :title="t('admin.nav.phases')">
    <template v-for="form in [...forms, fresh]" :key="form.id ?? 'new'">
      <h2 class="label accent mt-10">{{ form.id ? form.slug : t('admin.phases.new') }}</h2>
      <form class="panel mt-4" @submit.prevent="save(form)">
        <div class="grid-form">
          <label class="field"><span>{{ t('admin.phases.slug') }}</span><input v-model="form.slug" type="text" required></label>
          <label class="field"><span>{{ t('admin.phases.order') }}</span><input v-model.number="form.sort_order" type="number"></label>
          <label class="field"><span>{{ t('admin.phases.name_en') }}</span><input v-model="form.name_en" type="text"></label>
          <label class="field"><span>{{ t('admin.phases.name_zh') }}</span><input v-model="form.name_zh" type="text"></label>
          <label class="field full"><span>{{ t('admin.phases.description_en') }}</span><textarea v-model="form.description_en"></textarea></label>
          <label class="field full"><span>{{ t('admin.phases.description_zh') }}</span><textarea v-model="form.description_zh"></textarea></label>
          <label class="field"><span>{{ t('admin.phases.starts_at') }}</span><input v-model="form.starts_at" type="datetime-local"></label>
          <label class="field"><span>{{ t('admin.phases.ends_at') }}</span><input v-model="form.ends_at" type="datetime-local"></label>
          <label class="field"><span>{{ t('admin.phases.daily_limit') }}</span><input v-model.number="form.daily_limit" type="number" min="0"></label>
          <label class="field"><span>{{ t('admin.phases.leaderboard_mode') }}</span><select v-model="form.leaderboard_mode"><option v-for="m in MODES" :key="m" :value="m">{{ m }}</option></select></label>
          <div class="field full"><span>{{ t('admin.phases.scenarios') }}</span>
            <div class="flex flex-wrap gap-x-5">
              <label v-for="s in scenarios" :key="s.id" class="check inline-flex"><input v-model="form.scenario_ids" type="checkbox" :value="s.id"> {{ s.slug }}<template v-if="!s.weather_public"> ({{ t('common.hidden') }})</template></label>
              <span v-if="!scenarios.length" class="text3 text-sm">{{ t('common.no_data') }}</span>
            </div>
          </div>
        </div>
        <label class="check"><input v-model="form.allow_results" type="checkbox"> {{ t('admin.phases.allow_results') }}</label>
        <label class="check"><input v-model="form.allow_agents" type="checkbox"> {{ t('admin.phases.allow_agents') }}</label>
        <label class="check"><input v-model="form.counts_for_final" type="checkbox"> {{ t('admin.phases.counts_for_final') }}</label>
        <label class="check"><input v-model="form.is_active" type="checkbox"> {{ t('admin.phases.active') }}</label>
        <div class="actions-inline">
          <button class="btn primary sm" type="submit" :disabled="busy">{{ t('common.save') }}</button>
          <template v-if="form.id">
            <StatusPill v-if="form.status" :status="form.status" ns="leaderboard.status" />
            <button type="button" class="btn sm" :disabled="busy" @click="rescore(form)">{{ tf('admin.phases.rescore', { slug: form.slug }) }}</button>
            <button type="button" class="btn sm danger" :disabled="busy" @click="remove(form)">{{ t('common.delete') }}</button>
          </template>
        </div>
      </form>
    </template>
  </DashShell>
</template>
