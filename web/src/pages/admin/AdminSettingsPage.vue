<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { supabase } from '../../lib/supabase'
import { loadCreditsNote, loadPublicSettings } from '../../lib/data'
import { publicSiteUrl, BASE_URL } from '../../composables/api'
import { useRegistrationOpen } from '../../composables/useRegistrationOpen'
import { useAdmin } from '../../composables/useAdmin'
import { useTeamCapacity } from '../../composables/useTeamCapacity'
import DashShell from '../../components/layout/DashShell.vue'
import { competition, loadCompetition } from '../../stores/competition'
import { useI18n } from '../../composables/useI18n'

const { t, busy, run } = useAdmin()
const { pick } = useI18n()
const modeBusy=ref(false), modeError=ref('')
async function switchCompetition() {
  modeBusy.value=true; modeError.value=''
  try {
    const {error}=await supabase.rpc('set_competition_mode',{p_mode:competition.mode==='practice'?'competition':'practice'})
    if (error) { modeError.value=pick('The target competition is not ready. Check its scenarios and evaluation settings.','目标比赛尚未准备好，请先检查题目和评测配置。'); return }
    await loadCompetition(true)
    window.location.reload()
  } finally { modeBusy.value=false }
}
const { reload } = useRegistrationOpen()
const { reload: reloadCapacity } = useTeamCapacity()
const registrationOpen = ref(true)
const registrationDeadline = ref('')
const teamLimit = ref(150)
const creditsNote = ref({ en: '', zh: '' })

const toLocalInput = (iso: string | null) => {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}
const supabaseUrl = String(import.meta.env.VITE_SUPABASE_URL || '')

onMounted(async () => {
  const [settings, note, raw] = await Promise.all([
    loadPublicSettings(), loadCreditsNote(),
    supabase.from('site_settings').select('key, value').in('key', ['registration_open', 'team_limit']),
  ])
  // the toggle edits the manual flag itself, not the deadline-derived state
  const manual = (raw.data ?? []).find(row => row.key === 'registration_open')?.value
  registrationOpen.value = !(manual === false || manual === 'false')
  const limit = Number((raw.data ?? []).find(row => row.key === 'team_limit')?.value)
  if (Number.isInteger(limit) && limit >= 0) teamLimit.value = limit
  registrationDeadline.value = toLocalInput(settings.registrationDeadline)
  creditsNote.value = note
})
async function save() {
  const ok = await run(async () => {
    const deadline: string | boolean = registrationDeadline.value ? new Date(registrationDeadline.value).toISOString() : false
    const { error } = await supabase.from('site_settings').upsert([
      { key: 'registration_open', value: registrationOpen.value },
      { key: 'registration_deadline', value: deadline },
      { key: 'team_limit', value: Math.max(0, Math.floor(Number(teamLimit.value) || 0)) },
    ], { onConflict: 'key' })
    if (error) throw error
  }, t('admin.settings.saved'))
  if (ok) { await reload(); await reloadCapacity() }
}
async function saveCreditsNote() {
  await run(async () => {
    const value = { en: creditsNote.value.en.trim(), zh: creditsNote.value.zh.trim() }
    const { error } = await supabase.from('site_settings').upsert({ key: 'credits_note', value }, { onConflict: 'key' })
    if (error) throw error
  }, t('admin.settings.saved'))
}
</script>

<template>
  <DashShell admin :kicker="t('admin.kicker')" :title="t('admin.nav.settings')">
    <section class="panel max-w-2xl mb-8" data-testid="competition-mode-settings">
      <div class="hd"><h2>{{ pick('Current competition','当前比赛') }}</h2></div>
      <p class="text2">{{ competition.mode==='practice' ? pick('Practice','练习赛') : pick('Competition','正式比赛') }}</p>
      <p class="help mt-3">{{ pick('Participants see only this competition. Switching preserves all scores and keeps the same submission entry.','选手只看到当前比赛。切换后仍使用同一个提交入口，所有已有成绩保留。') }}</p>
      <p v-if="modeError" class="errors" role="alert">{{ modeError }}</p>
      <button class="btn primary sm mt-4" data-testid="competition-mode-switch" :disabled="modeBusy || busy" @click="switchCompetition">{{ competition.mode==='practice' ? pick('Switch to competition','切换为正式比赛') : pick('Switch to 练习赛','切换为练习赛') }}</button>
    </section>
    <form class="panel max-w-2xl" @submit.prevent="save">
      <label class="check"><input v-model="registrationOpen" type="checkbox"> {{ t('admin.settings.registration_open') }}</label>
      <label class="field mt-4"><span>{{ t('admin.settings.registration_deadline') }}</span>
        <input v-model="registrationDeadline" type="datetime-local" data-testid="settings-deadline">
      </label>
      <label class="field mt-4"><span>{{ pick('Team limit (hidden teams do not count; admins bypass)', '队伍上限（隐藏队伍不计；管理员不受限）') }}</span>
        <input v-model.number="teamLimit" type="number" min="0" step="1" data-testid="settings-team-limit">
      </label>
      <button class="btn primary sm" type="submit" :disabled="busy">{{ t('common.save') }}</button>
    </form>
    <form class="panel mt-8 max-w-2xl" @submit.prevent="saveCreditsNote">
      <div class="hd"><h2>{{ t('admin.settings.credits_note') }}</h2><router-link class="label accent" to="/admin/credits">{{ t('admin.nav.credits') }} →</router-link></div>
      <p class="text3 mb-4 text-sm">{{ t('admin.settings.credits_note_hint') }}</p>
      <label class="field"><span>{{ t('admin.settings.credits_note_en') }}</span><textarea data-testid="settings-credits-note-en" v-model="creditsNote.en" rows="3"></textarea></label>
      <label class="field"><span>{{ t('admin.settings.credits_note_zh') }}</span><textarea data-testid="settings-credits-note-zh" v-model="creditsNote.zh" rows="3"></textarea></label>
      <button data-testid="settings-credits-note-save" class="btn primary sm" type="submit" :disabled="busy">{{ t('common.save') }}</button>
    </form>
    <div class="panel mt-8 max-w-2xl">
      <div class="hd"><h2>{{ t('admin.settings.runtime') }}</h2></div>
      <dl class="kv">
        <dt>{{ t('admin.settings.site_url') }}</dt><dd class="m">{{ publicSiteUrl() }}</dd>
        <dt>{{ t('admin.settings.base_path') }}</dt><dd class="m">{{ BASE_URL }}</dd>
        <dt>{{ t('admin.settings.supabase_url') }}</dt><dd class="m">{{ supabaseUrl || '—' }}</dd>
      </dl>
    </div>
  </DashShell>
</template>
