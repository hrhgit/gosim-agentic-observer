<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { supabase } from '../../lib/supabase'
import { loadPhases, type Phase } from '../../lib/data'
import { fmtUtc, num } from '../../lib/format'
import { useAdmin } from '../../composables/useAdmin'
import DashShell from '../../components/layout/DashShell.vue'
import SkeletonRows from '../../components/layout/SkeletonRows.vue'
import StatusPill from '../../components/layout/StatusPill.vue'

const STATUSES = ['queued', 'running', 'scored', 'invalid', 'failed', 'cancelled']
const { t, busy, rpc, run } = useAdmin()
const phases = ref<Phase[]>([])
const rows = ref<any[]>([])
const loading = ref(true)
const filter = ref({ status: '', phase: '', team: '' })

async function load() {
  let query = supabase.from('submissions').select('id, kind, status, score, error, is_excluded, created_at, teams(name), profiles(email), phases(slug), scenarios(slug)').order('created_at', { ascending: false }).limit(300)
  if (filter.value.status) query = query.eq('status', filter.value.status)
  const { data } = await query
  let list = data ?? []
  if (filter.value.phase) list = list.filter((s: any) => s.phases?.slug === filter.value.phase)
  if (filter.value.team.trim()) { const q = filter.value.team.trim().toLowerCase(); list = list.filter((s: any) => String(s.teams?.name ?? '').toLowerCase().includes(q)) }
  rows.value = list
}
async function action(id: number, name: string) {
  const ok = await run(() => rpc('admin_submission_action', { p_id: id, p_action: name }), t('admin.done'))
  if (ok) await load()
}
onMounted(async () => { try { phases.value = await loadPhases(true); await load() } finally { loading.value = false } })
</script>

<template>
  <DashShell admin :kicker="t('admin.kicker')" :title="t('admin.nav.submissions')">
    <form class="actions-inline mb-6" @submit.prevent="load">
      <select v-model="filter.status" class="input w-auto"><option value="">{{ t('admin.submissions.any_status') }}</option><option v-for="s in STATUSES" :key="s" :value="s">{{ s }}</option></select>
      <select v-model="filter.phase" class="input w-auto"><option value="">{{ t('admin.submissions.any_phase') }}</option><option v-for="p in phases" :key="p.id" :value="p.slug">{{ p.slug }}</option></select>
      <input v-model="filter.team" type="text" class="input w-48" :placeholder="t('admin.submissions.team_filter')">
      <button class="btn sm" type="submit">{{ t('common.filter') }}</button>
    </form>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>#</th><th>{{ t('common.team') }}</th><th>{{ t('admin.submissions.user') }}</th><th>{{ t('leaderboard.phase') }}</th><th>{{ t('subs.kind') }}</th><th>{{ t('subs.scenario') }}</th><th>{{ t('common.status') }}</th><th class="r">{{ t('subs.score') }}</th><th>{{ t('subs.when') }}</th><th>{{ t('admin.submissions.excluded') }}</th><th>{{ t('admin.submissions.actions') }}</th></tr></thead>
        <tbody>
          <tr v-for="s in rows" :key="s.id">
            <td><router-link class="accent-l m" :to="`/submissions/${s.id}`">{{ s.id }}</router-link></td>
            <td>{{ s.teams?.name ?? '—' }}</td><td class="xs">{{ s.profiles?.email ?? '—' }}</td><td class="m xs">{{ s.phases?.slug ?? '—' }}</td><td>{{ s.kind }}</td><td class="m xs">{{ s.scenarios?.slug ?? '—' }}</td>
            <td><StatusPill :status="s.status" /><div v-if="s.error" class="text3 max-w-[20rem] text-xs">{{ String(s.error).slice(0, 120) }}</div></td>
            <td class="r m">{{ num(s.score) }}</td><td class="m xs">{{ fmtUtc(s.created_at, { short: true }) }}</td><td>{{ s.is_excluded ? t('common.yes') : '' }}</td>
            <td>
              <div class="actions-inline">
                <button type="button" class="copy-btn" :disabled="busy" @click="action(s.id, 'rescore')">{{ t('admin.submissions.rescore') }}</button>
                <button type="button" class="copy-btn" :disabled="busy" @click="action(s.id, 'exclude')">{{ s.is_excluded ? t('admin.submissions.include') : t('admin.submissions.exclude') }}</button>
                <button v-if="s.status === 'queued' || s.status === 'running'" type="button" class="copy-btn" :disabled="busy" @click="action(s.id, 'cancel')">{{ t('admin.submissions.cancel') }}</button>
              </div>
            </td>
          </tr>
          <tr v-if="loading"><td colspan="11" class="p-0"><SkeletonRows :rows="5" :cols="4" :label="t('common.loading')" /></td></tr>
          <tr v-else-if="!rows.length"><td colspan="11" class="text3">{{ t('common.no_data') }}</td></tr>
        </tbody>
      </table>
    </div>
  </DashShell>
</template>
