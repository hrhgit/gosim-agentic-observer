<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n, type Locale } from '../composables/useI18n'
import { supabase } from '../lib/supabase'
import { describeError } from '../lib/errors'
import { useAuth } from '../stores/auth'
import { useFlash } from '../stores/flash'
import { uploadMyAvatar, removeMyAvatar } from '../lib/avatar'
import { invalidateBoardCache } from '../lib/data'
import DashShell from '../components/layout/DashShell.vue'
import TierBadge from '../components/TierBadge.vue'
import UserAvatar from '../components/UserAvatar.vue'
import ApiTokensPanel from '../components/dashboard/ApiTokensPanel.vue'
import FriendsPanel from '../components/dashboard/FriendsPanel.vue'
import WechatQrPanel from '../components/wechat/WechatQrPanel.vue'

const { t, locale, setLocale } = useI18n()
const i18n = useI18n()
const flash = useFlash()
const { me, refreshMe } = useAuth()
const avatarInput = ref<HTMLInputElement | null>(null)
const avatarBusy = ref(false)
const form = ref({
  name: '', nickname: '', github: '', affiliation: '', role: '', seeking: '', seeking_count: 1, locale: 'zh' as Locale,
  astro_level: 0, ai_level: 0, city: '', contact: '', blurb: '', show_on_wall: false,
})
const astroTiers = computed(() => t('tiers.astro') as string[])
const aiTiers = computed(() => t('tiers.ai') as string[])
const roleOptions = computed(() => Object.entries(t('auth.role_options') as Record<string, string>))
const roleIsCustom = computed(() => Boolean(form.value.role) && !roleOptions.value.some(([code]) => code === form.value.role))
const pw = ref({ password: '', password2: '' })
const busy = ref(false)
const pwBusy = ref(false)
const loading = ref(true)

onMounted(async () => {
  const profile = await refreshMe()
  if (profile) form.value = {
    name: profile.name ?? '', nickname: profile.nickname ?? '', github: profile.github ?? '', affiliation: profile.affiliation ?? '', role: profile.role ?? '',
    seeking: profile.seeking ?? '', seeking_count: Number(profile.seeking_count) || 1, locale: (['zh', 'en', 'ja', 'fr'] as Locale[]).includes(profile.locale as Locale) ? profile.locale as Locale : locale.value,
    astro_level: Number(profile.astro_level ?? 0), ai_level: Number(profile.ai_level ?? 0),
    city: profile.city ?? '', contact: profile.contact ?? '', blurb: profile.blurb ?? '',
    show_on_wall: Boolean(profile.show_on_wall),
  }
  loading.value = false
})

async function save() {
  if (!me.value) return
  if (!form.value.name.trim()) { flash.error(t('auth.errors.name_required')); return }
  const nickname = form.value.nickname.trim()
  if (Array.from(nickname).length > 40) { flash.error(t('auth.errors.nickname_too_long')); return }
  busy.value = true
  try {
    const { error } = await supabase.from('profiles').update({
      name: form.value.name.trim(), nickname, github: form.value.github.trim().replace(/^@/, ''), affiliation: form.value.affiliation.trim(),
      role: form.value.role.trim(), locale: form.value.locale,
      seeking: form.value.seeking, seeking_count: form.value.seeking ? form.value.seeking_count : 0, looking_for_team: form.value.seeking !== '',
      astro_level: form.value.astro_level, ai_level: form.value.ai_level,
      city: form.value.city.trim(), contact: form.value.contact.trim(), blurb: form.value.blurb.trim().slice(0, 160),
      show_on_wall: form.value.show_on_wall,
    }).eq('id', me.value.id)
    if (error) throw error
    setLocale(form.value.locale)
    await refreshMe()
    form.value.nickname = nickname
    flash.success(t('flash.profile_saved'))
  } catch (e) { flash.error(describeError(e, i18n)) }
  finally { busy.value = false }
}

function pickAvatar() { avatarInput.value?.click() }

async function onAvatarChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !me.value) return
  avatarBusy.value = true
  try {
    await uploadMyAvatar(me.value.id, file)
    await refreshMe()
    invalidateBoardCache()
    flash.success(t('profile.avatar_updated'))
  } catch (e) { flash.error(describeError(e, i18n, ['profile.errors'])) }
  finally { avatarBusy.value = false }
}

async function removeAvatar() {
  if (!me.value || !me.value.avatar_url) return
  avatarBusy.value = true
  try {
    await removeMyAvatar(me.value.id, me.value.avatar_url)
    await refreshMe()
    invalidateBoardCache()
    flash.success(t('profile.avatar_removed'))
  } catch (e) { flash.error(describeError(e, i18n, ['profile.errors'])) }
  finally { avatarBusy.value = false }
}

async function changePassword() {
  if (pw.value.password.length < 8) { flash.error(t('auth.errors.password_too_short')); return }
  if (pw.value.password !== pw.value.password2) { flash.error(t('auth.errors.password_mismatch')); return }
  pwBusy.value = true
  try {
    const { error } = await supabase.auth.updateUser({ password: pw.value.password })
    if (error) throw error
    pw.value = { password: '', password2: '' }
    flash.success(t('flash.password_changed'))
  } catch (e) { flash.error(describeError(e, i18n, ['auth.errors'])) }
  finally { pwBusy.value = false }
}
</script>

<template>
  <DashShell :kicker="t('dash.title')" :title="t('profile.title')">
    <p v-if="loading" class="text3 text-sm">{{ t('common.loading') }}</p>
    <!-- Near the top, so it is easy to find (owner feedback); the profile form follows. -->
    <WechatQrPanel v-if="!loading" class="qr-top" />
    <div v-if="!loading" class="dash-grid">
      <div class="panel">
        <div class="hd"><h2>{{ t('profile.title') }}</h2><span class="m text3 text-sm">{{ me?.email }}</span></div>
        <div class="avatar-row mb-4" v-if="me">
          <UserAvatar :name="me.name" :github="me.github" :avatar-url="me.avatar_url" class="avatar-lg" />
          <div class="avatar-controls">
            <p class="label">{{ t('profile.avatar_title') }}</p>
            <div class="actions-inline">
              <button type="button" class="btn sm" :disabled="avatarBusy" @click="pickAvatar">{{ avatarBusy ? t('profile.avatar_uploading') : (me.avatar_url ? t('profile.avatar_change') : t('profile.avatar_upload')) }}</button>
              <button v-if="me.avatar_url" type="button" class="btn sm" :disabled="avatarBusy" @click="removeAvatar">{{ t('profile.avatar_remove') }}</button>
            </div>
            <small class="help">{{ t('profile.avatar_hint') }}</small>
            <input ref="avatarInput" type="file" accept="image/png,image/jpeg,image/webp" class="sr-only" @change="onAvatarChange">
          </div>
        </div>
        <form @submit.prevent="save" novalidate>
          <div class="grid-form">
            <label class="field"><span>{{ t('auth.name') }}</span><input data-testid="profile-name" v-model="form.name" type="text" required maxlength="120"></label>
            <label class="field"><span>{{ t('auth.nickname') }} · {{ t('common.optional') }}</span><input data-testid="profile-nickname" v-model="form.nickname" type="text" autocomplete="nickname" aria-describedby="profile-nickname-hint"><small id="profile-nickname-hint" class="help">{{ t('auth.nickname_hint') }}</small></label>
            <label class="field"><span>{{ t('auth.github') }}</span><input v-model="form.github" type="text" maxlength="120"></label>
            <label class="field"><span>{{ t('auth.affiliation') }}</span><input v-model="form.affiliation" type="text" maxlength="200"></label>
            <label class="field"><span>{{ t('profile.role') }}</span>
              <select v-model="form.role"><option value="">—</option><option v-for="[code, label] in roleOptions" :key="code" :value="code">{{ label }}</option><option v-if="roleIsCustom" :value="form.role">{{ form.role }}</option></select>
            </label>
            <label class="field"><span>{{ t('auth.city') }}</span><input v-model="form.city" type="text" maxlength="120"></label>
            <label class="field"><span>{{ t('auth.contact') }}</span><input v-model="form.contact" type="text" maxlength="200" :placeholder="t('auth.contact_ph')"></label>
            <label class="field"><span>{{ t('tiers.astro_label') }}</span>
              <select v-model.number="form.astro_level" data-testid="profile-astro"><option v-for="(n, i) in astroTiers" :key="i" :value="i">{{ n }}</option></select>
            </label>
            <label class="field"><span>{{ t('tiers.ai_label') }}</span>
              <select v-model.number="form.ai_level" data-testid="profile-ai"><option v-for="(n, i) in aiTiers" :key="i" :value="i">{{ n }}</option></select>
            </label>
            <label class="field"><span>{{ t('profile.language') }}</span>
              <select v-model="form.locale"><option value="zh">中文</option><option value="en">English</option><option value="ja">日本語</option><option value="fr">Français</option></select>
            </label>
          </div>
          <label class="field"><span>{{ t('auth.blurb') }}</span><input v-model="form.blurb" type="text" maxlength="160" :placeholder="t('auth.blurb_ph')"></label>
          <div class="wall-badges mb-4"><TierBadge kind="astro" :level="form.astro_level" /><TierBadge kind="ai" :level="form.ai_level" /></div>
          <label class="check"><input v-model="form.show_on_wall" type="checkbox"> {{ t('auth.show_on_wall') }}</label>
          <div class="grid-form">
            <label class="field"><span>{{ t('auth.seeking_label') }}</span>
              <select v-model="form.seeking">
                <option value="astro">{{ t('auth.seeking_astro') }}</option>
                <option value="ai">{{ t('auth.seeking_ai') }}</option>
                <option value="">{{ t('auth.seeking_none') }}</option>
              </select>
            </label>
            <label v-if="form.seeking" class="field"><span>{{ t('auth.seeking_count') }}</span>
              <select v-model.number="form.seeking_count"><option :value="1">1</option><option :value="2">2</option></select>
            </label>
          </div>
          <p class="help mb-4">{{ t('profile.email_note') }} {{ t('profile.locale_note') }}</p>
          <button data-testid="profile-save" class="btn primary sm" type="submit" :disabled="busy">{{ t('profile.save') }}</button>
        </form>
      </div>
      <div>
        <div class="panel">
          <div class="hd"><h2>{{ t('profile.password_title') }}</h2></div>
          <form @submit.prevent="changePassword" novalidate>
            <label class="field"><span>{{ t('profile.new') }}</span><input v-model="pw.password" type="password" minlength="8" autocomplete="new-password"></label>
            <label class="field"><span>{{ t('profile.new2') }}</span><input v-model="pw.password2" type="password" minlength="8" autocomplete="new-password"></label>
            <button class="btn sm" type="submit" :disabled="pwBusy">{{ t('profile.change') }}</button>
          </form>
        </div>
        <FriendsPanel />
        <ApiTokensPanel />
      </div>
    </div>
  </DashShell>
</template>

<style scoped>
.qr-top { margin-bottom: 2rem; }
</style>
