<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { supabase } from '../../lib/supabase'
import { fmtUtc, num } from '../../lib/format'
import { useAdmin } from '../../composables/useAdmin'
import { useWorkerStatus } from '../../composables/useWorkerStatus'
import DashShell from '../../components/layout/DashShell.vue'
import SkeletonRows from '../../components/layout/SkeletonRows.vue'
import StatusPill from '../../components/layout/StatusPill.vue'

const { t, tf, rpc, report } = useAdmin()
// Whether an evaluator is alive, so "nothing is being scored" does not require a trip to GitHub Actions.
const { heartbeat, ageSeconds, online, start: startWorkerWatch } = useWorkerStatus()
const stats = ref<Record<string, number>>({})
const recent = ref<any[]>([])
const audit = ref<any[]>([])
const loading = ref(true)
const keys = ['users', 'teams', 'submissions', 'queued', 'scored', 'failed']

onMounted(async () => {
  startWorkerWatch(null, 15000)
  try {
    const [s, r, a] = await Promise.all([
      rpc<Record<string, number>>('admin_stats'),
      supabase.from('submissions').select('id, kind, status, score, created_at, teams(name), phases(slug)').order('created_at', { ascending: false }).limit(15),
      supabase.from('audit_log').select('*').order('created_at', { ascending: false }).limit(25),
    ])
    stats.value = s ?? {}
    recent.value = r.data ?? []
    audit.value = a.data ?? []
  } catch (e) { report(e) }
  finally { loading.value = false }
})
</script>

<template>
  <DashShell admin :kicker="t('admin.kicker')" :title="t('admin.nav.overview')">
    <div class="stats stats-6">
      <div v-for="k in keys" :key="k" class="stat"><b>{{ stats[k] ?? '—' }}</b><span>{{ t(`admin.stats.${k}`) }}</span></div>
    </div>

    <div class="panel mt-8" data-testid="admin-worker">
      <div class="hd">
        <h2>{{ t('admin.worker.title') }}</h2>
        <span class="pill" :class="online ? 'accent' : 'failed'">{{ online ? t('admin.worker.online') : t('admin.worker.offline') }}</span>
      </div>
      <p v-if="!heartbeat" class="text3 text-sm">{{ t('admin.worker.never') }}</p>
      <template v-else>
        <div class="stats stats-4">
          <div class="stat"><b>{{ heartbeat.queued ?? '—' }}</b><span>{{ t('admin.worker.queued') }}</span></div>
          <div class="stat"><b>{{ heartbeat.processed >= 0 ? heartbeat.processed : '—' }}</b><span>{{ t('admin.worker.processed') }}</span></div>
          <div class="stat"><b>{{ ageSeconds ?? '—' }}s</b><span>{{ t('admin.worker.last_beat') }}</span></div>
          <div class="stat"><b>{{ heartbeat.busy ? t('admin.worker.busy') : t('admin.worker.idle') }}</b><span>{{ t('common.status') }}</span></div>
        </div>
        <p class="help mt-3 m xs">{{ heartbeat.worker_id }} · {{ (heartbeat.kinds ?? []).join(', ') }}</p>
      </template>
      <p v-if="!online" class="help mt-3">{{ tf('admin.worker.offline_hint', { minutes: 30 }) }}</p>
    </div>
    <div class="dash-grid mt-10">
      <div class="panel">
        <div class="hd"><h2>{{ t('admin.recent') }}</h2><router-link class="label accent" to="/admin/submissions">{{ t('admin.all') }} →</router-link></div>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>#</th><th>{{ t('common.team') }}</th><th>{{ t('leaderboard.phase') }}</th><th>{{ t('subs.kind') }}</th><th>{{ t('common.status') }}</th><th class="r">{{ t('subs.score') }}</th><th>{{ t('subs.when') }}</th></tr></thead>
            <tbody>
              <tr v-for="s in recent" :key="s.id">
                <td><router-link class="accent-l m" :to="`/submissions/${s.id}`">{{ s.id }}</router-link></td>
                <td>{{ s.teams?.name ?? '—' }}</td><td class="m xs">{{ s.phases?.slug ?? '—' }}</td><td>{{ s.kind }}</td>
                <td><StatusPill :status="s.status" /></td><td class="r m">{{ num(s.score) }}</td><td class="m xs">{{ fmtUtc(s.created_at, { short: true }) }}</td>
              </tr>
              <tr v-if="loading"><td colspan="7" class="p-0"><SkeletonRows :rows="5" :cols="4" :label="t('common.loading')" /></td></tr>
              <tr v-else-if="!recent.length"><td colspan="7" class="text3">{{ t('common.no_data') }}</td></tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <div class="hd"><h2>{{ t('admin.audit') }}</h2></div>
        <ul class="text-sm">
          <li v-for="a in audit" :key="a.id" class="border-b border-border-subtle py-2"><span class="m text3 text-xs">{{ fmtUtc(a.created_at, { short: true }) }}</span> {{ a.action }} <span class="text3 text-xs break-all">{{ typeof a.detail === 'string' ? a.detail : JSON.stringify(a.detail) }}</span></li>
          <li v-if="!audit.length" class="text3">{{ t('common.no_data') }}</li>
        </ul>
      </div>
    </div>
  </DashShell>
</template>
