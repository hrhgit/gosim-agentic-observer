<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { supabase } from '../../lib/supabase'
import { fmtUtc } from '../../lib/format'
import { useAdmin } from '../../composables/useAdmin'
import DashShell from '../../components/layout/DashShell.vue'

interface AnnForm { id: string | null; title_en: string; title_zh: string; body_en: string; body_zh: string; level: string; is_pinned: boolean; is_published: boolean; created_at?: string; notify_version?: number }
const LEVELS = ['info', 'warning', 'success']
const { t, busy, run, flash } = useAdmin()
const rows = ref<AnnForm[]>([])
const fresh = ref<AnnForm>(blank())

function blank(): AnnForm { return { id: null, title_en: '', title_zh: '', body_en: '', body_zh: '', level: 'info', is_pinned: false, is_published: true } }
async function load() {
  const { data, error } = await supabase.from('announcements').select('*').order('created_at', { ascending: false })
  if (error) throw error
  rows.value = ((data ?? []) as any[]).map(a => ({ id: a.id, title_en: a.title_en ?? '', title_zh: a.title_zh ?? '', body_en: a.body_en ?? '', body_zh: a.body_zh ?? '', level: a.level ?? 'info', is_pinned: Boolean(a.is_pinned), is_published: Boolean(a.is_published), created_at: a.created_at, notify_version: Number(a.notify_version) || 1 }))
}
async function save(form: AnnForm) {
  if (!form.title_en.trim() && !form.title_zh.trim()) { flash.error(t('auth.errors.name_required')); return }
  const payload = { title_en: form.title_en.trim() || form.title_zh.trim(), title_zh: form.title_zh.trim() || form.title_en.trim(), body_en: form.body_en, body_zh: form.body_zh, level: form.level, is_pinned: form.is_pinned, is_published: form.is_published }
  const ok = await run(async () => {
    const { error } = form.id ? await supabase.from('announcements').update(payload).eq('id', form.id) : await supabase.from('announcements').insert(payload)
    if (error) throw error
  }, t('admin.announcements.saved'))
  if (ok) { if (!form.id) fresh.value = blank(); await load() }
}
async function remove(form: AnnForm) {
  if (!form.id || !window.confirm(t('admin.announcements.delete_confirm'))) return
  const ok = await run(async () => { const { error } = await supabase.from('announcements').delete().eq('id', form.id!); if (error) throw error }, t('admin.announcements.deleted'))
  if (ok) await load()
}
// Editing never re-shows the popup; this does, once more for everyone (notify_version + 1).
async function renotify(form: AnnForm) {
  if (!form.id || !window.confirm(t('admin.announcements.renotify_confirm'))) return
  const ok = await run(async () => {
    const { error } = await supabase.rpc('renotify_announcement', { p_id: Number(form.id) })
    if (error) throw error
  }, t('admin.announcements.renotified'))
  if (ok) await load()
}
onMounted(() => load().catch(e => flash.error(String(e?.message ?? e))))
</script>

<template>
  <DashShell admin :kicker="t('admin.kicker')" :title="t('admin.nav.announcements')">
    <template v-for="form in [fresh, ...rows]" :key="form.id ?? 'new'">
      <h2 class="label accent mt-10">{{ form.id ? `#${String(form.id).slice(0, 8)}` : t('admin.announcements.new') }}</h2>
      <form class="panel mt-4" @submit.prevent="save(form)">
        <div class="grid-form">
          <label class="field"><span>{{ t('admin.announcements.title_en') }}</span><input :data-testid="form.id ? undefined : 'ann-title-en'" v-model="form.title_en" type="text"></label>
          <label class="field"><span>{{ t('admin.announcements.title_zh') }}</span><input :data-testid="form.id ? undefined : 'ann-title-zh'" v-model="form.title_zh" type="text"></label>
          <label class="field"><span>{{ t('admin.announcements.body_en') }}</span><textarea v-model="form.body_en"></textarea></label>
          <label class="field"><span>{{ t('admin.announcements.body_zh') }}</span><textarea v-model="form.body_zh"></textarea></label>
          <label class="field"><span>{{ t('admin.announcements.level') }}</span><select v-model="form.level"><option v-for="l in LEVELS" :key="l" :value="l">{{ l }}</option></select></label>
        </div>
        <label class="check"><input :data-testid="form.id ? undefined : 'ann-pinned'" v-model="form.is_pinned" type="checkbox"> {{ t('admin.announcements.pinned') }}</label>
        <label class="check"><input v-model="form.is_published" type="checkbox"> {{ t('admin.announcements.published') }}</label>
        <div class="actions-inline">
          <button :data-testid="form.id ? undefined : 'ann-save'" class="btn primary sm" type="submit" :disabled="busy">{{ t('common.save') }}</button>
          <template v-if="form.id">
            <button type="button" class="btn sm" :disabled="busy" data-testid="ann-renotify" :title="t('admin.announcements.renotify_help')" @click="renotify(form)">{{ t('admin.announcements.renotify') }}</button>
            <button type="button" class="btn sm danger" :disabled="busy" @click="remove(form)">{{ t('common.delete') }}</button>
            <span class="m text3 text-xs">{{ t('admin.announcements.notify_version') }} v{{ form.notify_version ?? 1 }}</span>
            <span class="m text3 text-xs">{{ fmtUtc(form.created_at) }} UTC</span>
          </template>
        </div>
      </form>
    </template>
  </DashShell>
</template>
