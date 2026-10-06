<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fmtUtc } from '../../lib/format'
import { useAdmin } from '../../composables/useAdmin'
import DashShell from '../../components/layout/DashShell.vue'
import SkeletonRows from '../../components/layout/SkeletonRows.vue'

const { t, busy, rpc, run } = useAdmin()
const incidents = ref<any[]>([])
const retrying = ref<{ revisions: any[]; runs: any[] }>({ revisions: [], runs: [] })
const loading = ref(true)

async function load() {
  incidents.value = await rpc('observer_admin_incidents')
  retrying.value = await rpc('observer_admin_retrying')
}
async function requeue(subjectType: string, subjectId: string) {
  const ok = await run(() => rpc('observer_admin_requeue', { p_subject_type: subjectType, p_subject_id: subjectId }), t('admin.incidents.requeued'))
  if (ok) await load()
}
onMounted(async () => { try { await load() } finally { loading.value = false } })
</script>

<template>
  <DashShell admin :kicker="t('admin.kicker')" :title="t('admin.nav.incidents')">
    <p class="help mb-6">{{ t('admin.incidents.intro') }}</p>
    <section class="panel mb-6">
      <h2>{{ t('admin.incidents.open_incidents') }}</h2>
      <div class="table-wrap mt-3">
        <table class="data-table">
          <thead><tr><th>{{ t('admin.incidents.since') }}</th><th>{{ t('common.team') }}</th><th>{{ t('admin.incidents.reason') }}</th><th></th></tr></thead>
          <tbody>
            <tr v-for="i in incidents" :key="i.id">
              <td class="m xs">{{ fmtUtc(i.created_at, { short: true }) }}</td>
              <td>{{ i.team_name ?? '—' }}</td>
              <td class="xs">{{ i.reason }} <span class="text3">({{ i.subject_type }})</span></td>
              <td><button type="button" class="copy-btn" :disabled="busy" @click="requeue(i.subject_type, i.subject_id)">{{ t('admin.incidents.requeue') }}</button></td>
            </tr>
            <tr v-if="loading"><td colspan="4" class="p-0"><SkeletonRows :rows="3" :cols="4" :label="t('common.loading')" /></td></tr>
            <tr v-else-if="!incidents.length"><td colspan="4" class="text3">{{ t('admin.incidents.no_incidents') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
    <section class="panel mb-6">
      <h2>{{ t('admin.incidents.retrying') }}</h2>
      <div class="table-wrap mt-3">
        <table class="data-table">
          <thead><tr><th>{{ t('admin.incidents.since') }}</th><th>{{ t('common.team') }}</th><th class="r">{{ t('admin.incidents.attempts') }}</th><th>{{ t('admin.incidents.status') }}</th><th></th></tr></thead>
          <tbody>
            <tr v-for="r in retrying.revisions" :key="'rev-'+r.revision_id">
              <td class="m xs">{{ fmtUtc(r.first_leased_at, { short: true }) }}</td>
              <td>{{ r.team_name ?? '—' }}</td>
              <td class="r m">{{ r.attempts }}</td>
              <td>{{ r.paused_at ? t('admin.incidents.parked') : '—' }}</td>
              <td><button v-if="r.paused_at" type="button" class="copy-btn" :disabled="busy" @click="requeue('revision', r.revision_id)">{{ t('admin.incidents.requeue') }}</button></td>
            </tr>
            <tr v-for="r in retrying.runs" :key="'run-'+r.run_id">
              <td class="m xs">{{ fmtUtc(r.first_leased_at, { short: true }) }}</td>
              <td>{{ r.team_name ?? '—' }}</td>
              <td class="r m">{{ r.attempts }}</td>
              <td>{{ r.paused_at ? t('admin.incidents.parked') : '—' }}</td>
              <td><button v-if="r.paused_at" type="button" class="copy-btn" :disabled="busy" @click="requeue('run', r.run_id)">{{ t('admin.incidents.requeue') }}</button></td>
            </tr>
            <tr v-if="loading"><td colspan="5" class="p-0"><SkeletonRows :rows="3" :cols="4" :label="t('common.loading')" /></td></tr>
            <tr v-else-if="!retrying.revisions.length && !retrying.runs.length"><td colspan="5" class="text3">{{ t('admin.incidents.no_retrying') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </DashShell>
</template>
