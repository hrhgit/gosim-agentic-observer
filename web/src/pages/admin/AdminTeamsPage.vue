<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAdmin } from '../../composables/useAdmin'
import { isFinalBoard, loadPhases, type Phase } from '../../lib/data'
import type { FinalVersion } from '../../lib/projectEvaluation'
import DashShell from '../../components/layout/DashShell.vue'
import SkeletonRows from '../../components/layout/SkeletonRows.vue'

const { t, busy, rpc, run, pick } = useAdmin()
const rows = ref<any[]>([])
// Final versions: the version each team sends to the hidden final evaluation.
type FinalRow = FinalVersion & { team_id: string; team_name: string; team_hidden: boolean; project_title: string | null
  chosen_by_github: string | null; model_mode: string; model_key_saved: boolean }
const finalPhases = ref<Phase[]>([]), finalPhase = ref(''), finals = ref<FinalRow[]>([])
const selectedFinalPhase = computed(() => finalPhases.value.find(p => p.id === finalPhase.value) ?? null)
async function loadFinals() { finals.value = finalPhase.value ? (await rpc<FinalRow[]>('observer_admin_final_versions', { p_phase: finalPhase.value })) ?? [] : [] }
const loading = ref(true)
const renames = ref<Record<string, string>>({})

async function load() { rows.value = (await rpc<any[]>('admin_teams')) ?? [] }
async function action(id: string, name: string, value: string | null = null) {
  if (name === 'rename' && !value?.trim()) return
  const ok = await run(() => rpc('admin_set_team', { p_team_id: id, p_action: name, p_value: value }), t('admin.done'), ['team.errors'])
  if (ok) { renames.value[id] = ''; await load() }
}
onMounted(async () => {
  try {
    await load()
    finalPhases.value = (await loadPhases(true)).filter(p => (p.counts_for_final || p.slug === 'online') && p.observer_settings?.projects_enabled && !isFinalBoard(p))
    finalPhase.value = finalPhases.value.find(p => p.slug === 'online')?.id ?? finalPhases.value[0]?.id ?? ''
    await loadFinals()
  } finally { loading.value = false }
})
</script>

<template>
  <DashShell admin :kicker="t('admin.kicker')" :title="t('admin.nav.teams')">
    <div class="table-wrap mt-2">
      <table class="data-table">
        <thead><tr><th>{{ t('common.name') }}</th><th class="r">{{ t('admin.teams.members') }}</th><th class="r">{{ t('admin.teams.subs') }}</th><th>{{ t('admin.teams.flags') }}</th><th>{{ t('admin.teams.code') }}</th><th>{{ t('common.actions') }}</th></tr></thead>
        <tbody>
          <tr v-for="team in rows" :key="team.id">
            <td>{{ team.name }}<div class="text3 text-xs">{{ team.slug }} {{ team.github_repo }}</div></td>
            <td class="r m">{{ team.member_count }}/{{ team.max_size }}</td>
            <td class="r m">{{ team.submission_count }}</td>
            <td><span v-if="team.is_hidden" class="pill">{{ t('admin.teams.hidden') }}</span> <span v-if="team.is_locked" class="pill">{{ t('admin.teams.locked') }}</span></td>
            <td class="m">{{ team.invite_code }}</td>
            <td>
              <div class="actions-inline">
                <button type="button" class="copy-btn" :disabled="busy" @click="action(team.id, 'toggle_hidden')">{{ team.is_hidden ? t('admin.teams.show') : t('admin.teams.hide') }}</button>
                <button type="button" class="copy-btn" :disabled="busy" @click="action(team.id, 'toggle_locked')">{{ team.is_locked ? t('admin.teams.unlock') : t('admin.teams.lock') }}</button>
                <input v-model="renames[team.id]" type="text" class="input w-36 px-2 py-1 text-sm" :placeholder="t('admin.teams.rename_placeholder')">
                <button type="button" class="copy-btn" :disabled="busy" @click="action(team.id, 'rename', renames[team.id] ?? '')">{{ t('admin.teams.rename') }}</button>
              </div>
            </td>
          </tr>
          <tr v-if="loading"><td colspan="6" class="p-0"><SkeletonRows :rows="5" :cols="4" :label="t('common.loading')" /></td></tr>
          <tr v-else-if="!rows.length"><td colspan="6" class="text3">{{ t('common.no_data') }}</td></tr>
        </tbody>
      </table>
    </div>
    <section class="mt-12" data-testid="admin-final-versions">
      <h2 class="text-lg font-semibold">{{ t('admin.teams.final_title') }}</h2>
      <p class="text3 mt-2 text-sm">{{ t('admin.teams.final_help') }}</p>
      <label v-if="finalPhases.length" class="mt-4 flex flex-wrap items-center gap-3 text-sm">{{ t('admin.teams.final_phase') }}
        <select v-model="finalPhase" class="input px-2 py-1" @change="loadFinals"><option v-for="p in finalPhases" :key="p.id" :value="p.id">{{ pick(p.name_en, p.name_zh) }} ({{ p.slug }})</option></select>
        <span v-if="selectedFinalPhase?.ends_at" class="text3">{{ new Date(selectedFinalPhase.ends_at) <= new Date() ? t('admin.teams.final_locked') : t('admin.teams.final_open') + ' ' + new Date(selectedFinalPhase.ends_at).toLocaleString() }}</span>
      </label>
      <div class="table-wrap mt-4">
        <table class="data-table">
          <thead><tr><th>{{ t('common.name') }}</th><th>{{ t('admin.teams.final_version') }}</th><th>{{ t('admin.teams.final_source') }}</th><th class="r">{{ t('admin.teams.final_score') }}</th><th>{{ t('admin.teams.final_model') }}</th></tr></thead>
          <tbody>
            <tr v-for="f in finals" :key="f.team_id">
              <td>{{ f.team_name }} <span v-if="f.team_hidden" class="pill">{{ t('admin.teams.hidden') }}</span></td>
              <td><template v-if="f.revision_id">{{ f.project_title }}<div class="text3 m text-xs">{{ f.revision_id }}</div></template><span v-else class="text3">—</span></td>
              <td>{{ f.source === 'chosen' ? t('admin.teams.final_chosen') + (f.chosen_by_github ? ' · @' + f.chosen_by_github : '') + (f.chosen_at ? ' · ' + new Date(f.chosen_at).toLocaleString() : '') : f.source === 'best' ? t('admin.teams.final_best') : '—' }}</td>
              <td class="r m">{{ f.best_score != null ? f.best_score.toFixed(2) : '—' }}</td>
              <td class="m text-xs">{{ f.model_mode }}{{ f.model_key_saved ? ' · key' : '' }}</td>
            </tr>
            <tr v-if="!finals.length"><td colspan="5" class="text3">{{ t('common.no_data') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </DashShell>
</template>
