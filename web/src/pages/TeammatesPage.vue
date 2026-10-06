<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from '../composables/useI18n'
import { isSupabaseConfigured, supabase } from '../lib/supabase'
import { describeError } from '../lib/errors'
import { loadParticipantsWall, revealTeammateContact, type WallEntry } from '../lib/data'
import { useAuth, initAuth, refreshMe } from '../stores/auth'
import { useFlash } from '../stores/flash'
import TierBadge from '../components/TierBadge.vue'
import UserAvatar from '../components/UserAvatar.vue'
import TeamDirectory from '../components/TeamDirectory.vue'
import WechatGroup from '../components/WechatGroup.vue'
import { teamAction } from '../stores/teamNotifications'
import WechatQrButton from '../components/wechat/WechatQrButton.vue'
import FindByUid from '../components/FindByUid.vue'
import WechatQrPrompt from '../components/wechat/WechatQrPrompt.vue'
import { visibleWechatQrs } from '../lib/wechatQrApi'

const { t, tf } = useI18n()
const i18n = useI18n()
const flash = useFlash()
const { me, team, isLoggedIn } = useAuth()

const entries = ref<WallEntry[]>([])
const loading = ref(true)
const minAstro = ref(0)
const minAi = ref(0)
const lookingOnly = ref(false)
const complementary = ref(true)
const contacts = ref<Record<string, { contact: string; github: string } | null>>({})
const contactBusy = ref<string | null>(null)
/** Signed-in viewers only: whose WeChat QR this viewer may open (owner's choice: friends or everyone). */
const qrs = ref<Record<string, string>>({})
const inviteBusy = ref(''), invited = ref(new Set<string>())
async function invite(e: WallEntry) {
  if (inviteBusy.value || invited.value.has(e.id)) return
  inviteBusy.value=e.id
  try { await teamAction('send_team_invite',{p_recipient:e.id}); invited.value.add(e.id) }
  catch(err) { flash.error(describeError(err,i18n,['team.errors','team'])) }
  finally { inviteBusy.value='' }
}

const wallForm = ref({ show_on_wall: false, blurb: '', contact: '', seeking: '', seeking_count: 1 })
const wallBusy = ref(false)

onMounted(async () => {
  await initAuth()
  if (isLoggedIn.value) await syncWallForm()
  await reload()
  if (isLoggedIn.value) {
    const {data} = await supabase.rpc('my_team_invitations')
    for (const item of data ?? []) if (item.direction==='sent' && item.kind==='invite' && item.status==='pending') invited.value.add(item.recipient_id)
  }
})

async function syncWallForm() {
  const profile = me.value ?? await refreshMe()
  if (profile) wallForm.value = {
    show_on_wall: Boolean(profile.show_on_wall),
    blurb: profile.blurb ?? '',
    contact: profile.contact ?? '',
    seeking: profile.seeking ?? '', seeking_count: Number(profile.seeking_count) || 1,
  }
}

async function reload() {
  if (!isSupabaseConfigured) { loading.value = false; return }
  loading.value = true
  try {
    entries.value = await loadParticipantsWall(200)
    if (isLoggedIn.value) qrs.value = await visibleWechatQrs(entries.value.map(e => e.id)).catch(() => ({}))
  }
  catch (e) { flash.error(describeError(e, i18n)) }
  finally { loading.value = false }
}

async function saveWall() {
  if (!me.value) return
  wallBusy.value = true
  try {
    const { error } = await supabase.from('profiles').update({
      show_on_wall: wallForm.value.show_on_wall,
      blurb: wallForm.value.blurb.trim().slice(0, 160),
      contact: wallForm.value.contact.trim().slice(0, 200),
      seeking: wallForm.value.seeking,
      seeking_count: wallForm.value.seeking ? wallForm.value.seeking_count : 0,
      looking_for_team: wallForm.value.seeking !== '',
    }).eq('id', me.value.id)
    if (error) throw error
    await refreshMe()
    flash.success(t(wallForm.value.show_on_wall ? 'teammates.saved_on' : 'teammates.saved_off'))
    await reload()
  } catch (e) { flash.error(describeError(e, i18n)) }
  finally { wallBusy.value = false }
}

// Complementarity: how much stronger they are where I am weaker, on both axes.
function compScore(e: WallEntry): number {
  if (!me.value) return 0
  return Math.max(0, e.astro_level - me.value.astro_level) + Math.max(0, e.ai_level - me.value.ai_level)
}

const filtered = computed(() => {
  let list = entries.value.filter(e => e.astro_level >= minAstro.value && e.ai_level >= minAi.value)
  if (lookingOnly.value) list = list.filter(e => e.looking_for_team)
  if (isLoggedIn.value && complementary.value) {
    list = [...list].sort((a, b) => compScore(b) - compScore(a) || +b.looking_for_team - +a.looking_for_team)
  }
  return list
})

async function reveal(e: WallEntry) {
  if (!isLoggedIn.value) return
  contactBusy.value = e.id
  try { contacts.value = { ...contacts.value, [e.id]: await revealTeammateContact(e.id) } }
  catch (err) { flash.error(describeError(err, i18n)) }
  finally { contactBusy.value = null }
}

function lookingChip(e: WallEntry): string {
  if (e.seeking === 'astro') return tf('home.participants.looking_astro', { n: e.seeking_count || 1 })
  if (e.seeking === 'ai') return tf('home.participants.looking_ai', { n: e.seeking_count || 1 })
  return e.looking_for_team ? t('home.participants.looking') : ''
}
const roleLabel = (role: string | null) => {
  if (!role) return ''
  const known = t('auth.role_options') as Record<string, string>
  return known[role] ?? role
}
const tierNames = (kind: 'astro' | 'ai') => t(`tiers.${kind}`) as string[]
</script>

<template>
  <main class="poster-canvas min-h-[80vh]">
    <section class="section"><div class="wrap">
      <span class="poster-kicker kicker-amber">{{ t('teammates.kicker') }}</span>
      <h1 class="section-title distressed-type mt-6">{{ t('teammates.title') }}</h1>
      <p class="lede mt-6 max-w-3xl">{{ t('teammates.lede') }}</p>
      <WechatQrPrompt class="mt-8" />
      <FindByUid class="mt-8" />

      <div class="panel mt-8" data-testid="team-actions">
        <div class="hd"><h2>{{ t('team.title') }}</h2></div>
        <p class="text2 text-sm">{{ team ? t('team.manage_hint') : t('team.entry_hint') }}</p>
        <div class="actions-inline mt-4">
          <template v-if="team">
            <router-link class="btn primary sm" to="/team#invite">{{ t('team.invite_people') }} →</router-link>
            <router-link class="btn sm" to="/team">{{ t('nav.team') }} →</router-link>
          </template>
          <template v-else>
            <router-link class="btn primary sm" to="/team#create">{{ t('team.create') }} →</router-link>
            <router-link class="btn sm" to="/team#join">{{ t('team.join') }} →</router-link>
          </template>
        </div>
      </div>

      <div class="panel mt-8"><WechatGroup /></div>

      <TeamDirectory />

      <div v-if="isLoggedIn" class="wall-self panel mt-10" data-testid="wall-self">
        <div class="hd"><h2>{{ t('teammates.self_title') }}</h2>
          <span class="label" :class="wallForm.show_on_wall ? 'accent-emerald' : ''">{{ wallForm.show_on_wall ? t('teammates.self_on') : t('teammates.self_off') }}</span>
        </div>
        <div class="grid-form">
          <label class="field"><span>{{ t('teammates.self_blurb') }}</span><input v-model="wallForm.blurb" type="text" maxlength="160" :placeholder="t('teammates.self_blurb_ph')"></label>
          <label class="field"><span>{{ t('auth.contact') }}</span><input v-model="wallForm.contact" type="text" maxlength="200" :placeholder="t('auth.contact_ph')"></label>
        </div>
        <label class="check"><input v-model="wallForm.show_on_wall" type="checkbox" data-testid="wall-toggle"> {{ t('auth.show_on_wall') }}</label>
        <div class="grid-form">
          <label class="field"><span>{{ t('auth.seeking_label') }}</span>
            <select v-model="wallForm.seeking">
              <option value="astro">{{ t('auth.seeking_astro') }}</option>
              <option value="ai">{{ t('auth.seeking_ai') }}</option>
              <option value="">{{ t('auth.seeking_none') }}</option>
            </select>
          </label>
          <label v-if="wallForm.seeking" class="field"><span>{{ t('auth.seeking_count') }}</span>
            <select v-model.number="wallForm.seeking_count"><option :value="1">1</option><option :value="2">2</option></select>
          </label>
        </div>
        <button class="btn primary sm" type="button" :disabled="wallBusy" @click="saveWall">{{ t('teammates.self_save') }}</button>
      </div>
      <div v-else class="notice mt-10">
        {{ t('teammates.login_hint') }}
        <router-link class="accent-l underline underline-offset-2" to="/register">{{ t('nav.register') }} →</router-link>
      </div>

      <div v-if="entries.length >= 3" class="wall-filters mt-12" data-testid="wall-filters">
        <label class="field slim"><span>{{ t('teammates.filter_astro') }}</span>
          <select v-model.number="minAstro"><option v-for="(n, i) in tierNames('astro')" :key="i" :value="i">{{ i === 0 ? t('teammates.filter_any') : `≥ ${n}` }}</option></select>
        </label>
        <label class="field slim"><span>{{ t('teammates.filter_ai') }}</span>
          <select v-model.number="minAi"><option v-for="(n, i) in tierNames('ai')" :key="i" :value="i">{{ i === 0 ? t('teammates.filter_any') : `≥ ${n}` }}</option></select>
        </label>
        <label class="check"><input v-model="lookingOnly" type="checkbox"> {{ t('teammates.filter_looking') }}</label>
        <label v-if="isLoggedIn" class="check"><input v-model="complementary" type="checkbox"> {{ t('teammates.filter_comp') }}</label>
      </div>

      <p v-if="loading" class="text3 mt-10 text-sm">{{ t('common.loading') }}</p>
      <div v-else-if="entries.length === 0" class="wall-empty mt-12">
        <p class="wall-empty-title">{{ t('home.participants.empty_title') }}</p>
        <p class="mt-2 text-sm text-[#9aa3b8]">{{ t('home.participants.empty_desc') }}</p>
      </div>
      <div v-else-if="filtered.length" class="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3" data-testid="wall-grid">
        <article v-for="e in filtered" :key="e.id" v-tilt class="wall-card wall-card-lg">
          <div class="wall-card-head">
            <UserAvatar :name="e.name" :github="e.github" :avatar-url="e.avatar_url" /><h3>{{ e.name }}</h3>
            <span v-if="isLoggedIn && complementary && compScore(e) >= 2" class="wall-comp">{{ t('teammates.comp_chip') }}</span>
            <span v-else-if="lookingChip(e)" class="wall-looking"><span class="live-dot h-1.5 w-1.5"></span>{{ lookingChip(e) }}</span>
          </div>
          <div class="wall-badges"><TierBadge kind="astro" :level="e.astro_level" /><TierBadge kind="ai" :level="e.ai_level" /></div>
          <p v-if="e.blurb" class="wall-blurb">“{{ e.blurb }}”</p>
          <p class="wall-meta">{{ [roleLabel(e.role), e.affiliation, e.city].filter(Boolean).join(' · ') || '—' }}</p>
          <p class="wall-meta">{{ e.team_name ? tf('home.participants.in_team', { team: e.team_name }) : t('teammates.no_team') }}</p>
          <div class="mt-4">
            <div v-if="isLoggedIn && e.id !== me?.id" class="mb-3">
              <router-link v-if="invited.has(e.id)" class="copy-btn" to="/notifications">{{ i18n.pick('Invitation sent · view progress','已邀请 · 查看进度') }}</router-link>
              <button v-else-if="team && !e.team_name" class="btn primary sm" :disabled="!!inviteBusy" @click="invite(e)">{{ inviteBusy===e.id ? t('common.working') : i18n.pick('Invite','邀请') }}</button>
              <span v-else class="text3 text-sm">{{ e.team_name ? i18n.pick('Already in a team','已有队伍') : i18n.pick('Create a team to invite','创建队伍后可邀请') }}</span>
            </div>
            <template v-if="isLoggedIn">
              <div v-if="e.id in contacts" class="wall-contact">
                <template v-if="contacts[e.id] && (contacts[e.id]!.contact || contacts[e.id]!.github)">
                  <p v-if="contacts[e.id]!.contact"><span class="label">{{ t('auth.contact') }}</span> {{ contacts[e.id]!.contact }}</p>
                  <p v-if="contacts[e.id]!.github"><span class="label">GitHub</span> {{ contacts[e.id]!.github }}</p>
                </template>
                <p v-else class="text3 text-sm">{{ t('teammates.no_contact') }}</p>
              </div>
              <button v-else type="button" class="copy-btn" :disabled="contactBusy === e.id" @click="reveal(e)">{{ contactBusy === e.id ? t('common.working') : t('teammates.reveal') }}</button>
              <WechatQrButton v-if="qrs[e.id]" class="ml-2" :user-id="e.id" :path="qrs[e.id]!" :name="e.name" :self="e.id === me?.id" />
            </template>
            <router-link v-else class="copy-btn inline-block" to="/register?mode=login">{{ t('teammates.login_to_contact') }}</router-link>
          </div>
        </article>
      </div>
      <div v-else class="wall-empty mt-10">
        <p class="wall-empty-title">{{ t('teammates.empty_title') }}</p>
        <p class="mt-2 text-sm text-[#9aa3b8]">{{ t('teammates.empty_desc') }}</p>
      </div>
    </div></section>
  </main>
</template>
