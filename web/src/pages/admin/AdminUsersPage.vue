<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fmtUtc } from '../../lib/format'
import { supabase } from '../../lib/supabase'
import { useAdmin } from '../../composables/useAdmin'
import DashShell from '../../components/layout/DashShell.vue'
import SkeletonRows from '../../components/layout/SkeletonRows.vue'
import AdminWechatQrReports from '../../components/wechat/AdminWechatQrReports.vue'
import AdminDmReports from '../../components/dm/AdminDmReports.vue'

const { t, busy, rpc, run } = useAdmin()
const rows = ref<any[]>([])
const loading = ref(true)
const q = ref('')

async function load() { rows.value = (await rpc<any[]>('admin_users', { p_query: q.value.trim() || null })) ?? [] }
async function action(id: string, name: string) {
  if (name === 'toggle_ban' && !window.confirm(t('admin.users.confirm_ban'))) return
  if (name === 'toggle_admin' && !window.confirm(t('admin.users.confirm_admin'))) return
  const ok = await run(() => rpc('admin_set_user', { p_user_id: id, p_action: name }), t('admin.done'))
  if (ok) await load()
}

/** Mint a password-reset link and copy it, so a participant who never received the mail can still get in.
 *  The project has no custom SMTP, so the built-in mailer only allows two messages an hour project-wide. */
const linkFor = ref<Record<string, string>>({})
async function recoveryLink(email: string) {
  await run(async () => {
    const { data, error } = await supabase.functions.invoke('recovery-link', { body: { email } })
    if (error) throw error
    const link = (data as { action_link?: string })?.action_link ?? ''
    if (!link) throw new Error('no_link_returned')
    linkFor.value = { ...linkFor.value, [email]: link }
    try { await navigator.clipboard.writeText(link) } catch { /* clipboard blocked: the link is shown below */ }
  }, t('admin.users.link_ready'), ['admin.users'])
}
onMounted(async () => { try { await load() } finally { loading.value = false } })
</script>

<template>
  <DashShell admin :kicker="t('admin.kicker')" :title="t('admin.nav.users')">
    <AdminWechatQrReports />
    <AdminDmReports />
    <form class="actions-inline mb-6" @submit.prevent="load">
      <input v-model="q" type="text" class="input w-64" :placeholder="t('admin.users.placeholder')">
      <button class="btn sm" type="submit">{{ t('common.search') }}</button>
    </form>
    <div class="table-wrap">
      <table class="data-table">
        <thead><tr><th>{{ t('common.email') }}</th><th>{{ t('common.name') }}</th><th>{{ t('common.team') }}</th><th>{{ t('admin.users.flags') }}</th><th>{{ t('admin.users.joined') }}</th><th>{{ t('common.actions') }}</th></tr></thead>
        <tbody>
          <tr v-for="u in rows" :key="u.id">
            <td class="text-sm">{{ u.email }}</td>
            <td>{{ u.name }}<div class="text3 text-xs">{{ u.affiliation }} {{ u.github }}</div></td>
            <td>{{ u.team_name ?? '—' }}</td>
            <td><span v-if="u.is_admin" class="pill accent">{{ t('admin.users.admin') }}</span> <span v-if="u.is_banned" class="pill failed">{{ t('admin.users.banned') }}</span></td>
            <td class="m xs">{{ fmtUtc(u.created_at).slice(0, 10) }}</td>
            <td>
              <div class="actions-inline">
                <button type="button" class="copy-btn" :disabled="busy" @click="action(u.id, 'toggle_admin')">{{ t('admin.users.toggle_admin') }}</button>
                <button type="button" class="copy-btn" :disabled="busy" @click="action(u.id, 'toggle_ban')">{{ t('admin.users.toggle_ban') }}</button>
                <button type="button" class="copy-btn" :disabled="busy || !u.team_name" @click="action(u.id, 'remove_from_team')">{{ t('admin.users.remove_from_team') }}</button>
                <button type="button" class="copy-btn" :disabled="busy" @click="recoveryLink(u.email)">{{ t('admin.users.recovery_link') }}</button>
              </div>
              <p v-if="linkFor[u.email]" class="text3 xs mt-1 break-all" style="max-width: 24rem">
                {{ t('admin.users.link_hint') }}<br><span class="m">{{ linkFor[u.email] }}</span>
              </p>
            </td>
          </tr>
          <tr v-if="loading"><td colspan="6" class="p-0"><SkeletonRows :rows="5" :cols="4" :label="t('common.loading')" /></td></tr>
          <tr v-else-if="!rows.length"><td colspan="6" class="text3">{{ t('common.no_data') }}</td></tr>
        </tbody>
      </table>
    </div>
  </DashShell>
</template>
