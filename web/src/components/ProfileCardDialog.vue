<script setup lang="ts">
// A person's or a team's card, opened from team requests and invitations (stores/profileCard). Read-only: the
// backend returns only wall-type fields, and the WeChat QR only when its owner's visibility allows this viewer.
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from '../composables/useI18n'
import { describeError } from '../lib/errors'
import { supabase } from '../lib/supabase'
import { githubUrl, normalizePersonCard, normalizeTeamCard, type PersonCard, type TeamCard } from '../lib/cards'
import { closeProfileCard, openPersonCard, openTeamCard, profileCard } from '../stores/profileCard'
import { foundExtra, type FoundExtra } from '../lib/friends'
import { inviteToTeamByUid, sendFriendRequest } from '../lib/friendsApi'
import { useAuth } from '../stores/auth'
import { useFlash } from '../stores/flash'
import UserAvatar from './UserAvatar.vue'
import TierBadge from './TierBadge.vue'
import WechatQrButton from './wechat/WechatQrButton.vue'

const i18n = useI18n(), { t, tf, pick } = i18n
const dialog = ref<HTMLDialogElement | null>(null)
const person = ref<PersonCard | null>(null), team = ref<TeamCard | null>(null)
const loading = ref(false), error = ref('')
/** Set when the card was found by UID: what the viewer may do from it. */
const found = ref<FoundExtra | null>(null)
const acting = ref(false), done = ref<Record<string, boolean>>({})
const { me, team: myTeam } = useAuth()
const flash = useFlash()
const isCaptain = computed(() => Boolean(myTeam.value && me.value && myTeam.value.leader_id === me.value.id))
async function act(kind: 'friend' | 'invite') {
  if (!found.value || acting.value) return
  acting.value = true
  try {
    if (kind === 'friend') { const status = await sendFriendRequest(found.value.uid); flash.success(t(`friends.result.${status}`)) }
    else { await inviteToTeamByUid(found.value.uid); flash.success(t('friends.invited')) }
    done.value = { ...done.value, [kind]: true }
  } catch (e) { flash.error(describeError(e, i18n, ['friends.errors', 'team.errors'])) }
  finally { acting.value = false }
}
/** The team card this person card was opened from, for "back". */
const fromTeam = ref<string | null>(null)

async function load(kind: 'person' | 'team' | 'uid', id: string) {
  loading.value = true; error.value = ''; person.value = null; found.value = null; done.value = {}
  if (kind === 'team') team.value = null
  try {
    if (kind === 'uid') {
      const { data, error: e } = await supabase.rpc('find_by_uid', { p_uid: Number(id) })
      if (e) throw e
      found.value = foundExtra(data)
      person.value = normalizePersonCard(data)
      return
    }
    const { data, error: e } = await supabase.rpc(kind === 'person' ? 'person_card' : 'team_card', kind === 'person' ? { p_user: id } : { p_team: id })
    if (e) throw e
    if (kind === 'person') person.value = normalizePersonCard(data)
    else team.value = normalizeTeamCard(data)
    if (!person.value && !team.value) throw new Error('card_not_available')
  } catch (e) {
    error.value = describeError(e, i18n, ['cards', 'friends.errors'])
  } finally { loading.value = false }
}

watch(() => profileCard.serial, async () => {
  const { kind, id } = profileCard
  if (!kind) return
  if (kind === 'team' || kind === 'uid') fromTeam.value = null
  else if (!(team.value && team.value.members.some(m => m.id === id))) { fromTeam.value = null; team.value = null }
  else fromTeam.value = team.value.id
  await nextTick()
  if (dialog.value && !dialog.value.open) dialog.value.showModal()
  await load(kind, id)
})

function close() { dialog.value?.close() }
function onClose() { closeProfileCard(); person.value = null; found.value = null; team.value = null; fromTeam.value = null; error.value = '' }
function back() { if (fromTeam.value) openTeamCard(fromTeam.value) }

const lookingChip = computed(() => {
  const p = person.value
  if (!p) return ''
  if (p.seeking === 'astro') return tf('home.participants.looking_astro', { n: p.seeking_count })
  if (p.seeking === 'ai') return tf('home.participants.looking_ai', { n: p.seeking_count })
  return p.looking_for_team ? t('home.participants.looking') : ''
})
const roleLabel = (role: string | null) => {
  if (!role) return ''
  const known = t('auth.role_options') as Record<string, string>
  return known[role] ?? role
}
const meta = computed(() => person.value ? [roleLabel(person.value.role), person.value.affiliation, person.value.city].filter(Boolean).join(' · ') : '')
const showingTeam = computed(() => profileCard.kind === 'team')
</script>

<template>
  <dialog ref="dialog" class="card-dialog" :aria-label="pick('Profile card', '名片')" data-testid="profile-card" @click.self="close" @close="onClose">
    <div class="card-panel">
      <button type="button" class="card-close" :aria-label="pick('Close', '关闭')" @click="close">×</button>
      <button v-if="!showingTeam && fromTeam" type="button" class="card-back" data-testid="profile-card-back" @click="back">← {{ pick('Team', '队伍') }}</button>
      <p class="label">{{ showingTeam ? pick('Team card', '队伍名片') : pick('Profile card', '个人名片') }}<template v-if="found"> · <span class="mono" translate="no">UID {{ found.uid }}</span></template></p>
      <p v-if="loading" class="text3 mt-4" role="status">{{ t('common.loading') }}</p>
      <p v-else-if="error" class="text3 mt-4" role="alert" data-testid="profile-card-error">{{ error }}</p>

      <template v-else-if="!showingTeam && person">
        <div class="card-head">
          <UserAvatar :name="person.name" :github="person.github" :avatar-url="person.avatar_url" />
          <div class="min-w-0">
            <h2 class="card-name" data-testid="profile-card-name">{{ person.name }}</h2>
            <p class="card-meta">{{ person.team_name ? tf('home.participants.in_team', { team: person.team_name }) : t('teammates.no_team') }}</p>
          </div>
        </div>
        <div class="wall-badges mt-3"><TierBadge kind="astro" :level="person.astro_level" /><TierBadge kind="ai" :level="person.ai_level" /></div>
        <p v-if="lookingChip" class="wall-looking mt-3"><span class="live-dot h-1.5 w-1.5"></span>{{ lookingChip }}</p>
        <p v-if="person.blurb" class="card-blurb" data-testid="profile-card-blurb">“{{ person.blurb }}”</p>
        <p v-else class="text3 text-sm mt-3">{{ pick('No introduction yet.', '还没有写自我介绍。') }}</p>
        <p v-if="meta" class="card-meta mt-2">{{ meta }}</p>
        <dl class="card-fields">
          <template v-if="person.github"><dt>GitHub</dt><dd><a v-if="githubUrl(person.github)" :href="githubUrl(person.github)!" target="_blank" rel="noopener noreferrer" class="accent-l underline underline-offset-2">{{ person.github }}</a><span v-else>{{ person.github }}</span></dd></template>
          <template v-if="person.contact"><dt>{{ t('auth.contact') }}</dt><dd>{{ person.contact }}</dd></template>
        </dl>
        <WechatQrButton v-if="person.wechat_qr" class="mt-3" :user-id="person.id" :path="person.wechat_qr" :name="person.name" />
        <div v-if="found && !found.self" class="card-actions" data-testid="uid-card-actions">
          <span v-if="found.is_friend" class="text3 text-sm">{{ t('friends.already') }}</span>
          <button v-else type="button" class="btn sm primary" data-testid="uid-card-add-friend" :disabled="acting || done.friend" @click="act('friend')">{{ done.friend ? t('friends.request_sent_short') : t('friends.add_friend') }}</button>
          <button v-if="isCaptain && !found.in_team" type="button" class="btn sm" data-testid="uid-card-invite" :disabled="acting || done.invite" @click="act('invite')">{{ done.invite ? t('friends.invited_short') : t('friends.invite_team') }}</button>
        </div>
        <p v-else-if="found && found.self" class="text3 text-sm mt-4">{{ t('friends.this_is_you') }}</p>
      </template>

      <template v-else-if="showingTeam && team">
        <h2 class="card-name mt-1" data-testid="profile-card-name">{{ team.name }}</h2>
        <p class="card-meta">{{ team.members.length }} / {{ team.max_size }}{{ team.is_locked ? ' · ' + pick('Locked', '已锁定') : '' }}</p>
        <p v-if="team.project_idea" class="card-blurb" data-testid="profile-card-idea">{{ team.project_idea }}</p>
        <p v-else class="text3 text-sm mt-3">{{ pick('No project idea written yet.', '还没有写项目设想。') }}</p>
        <p class="label mt-5">{{ pick('Members', '成员') }}</p>
        <ul class="card-members">
          <li v-for="m in team.members" :key="m.id">
            <button type="button" class="card-member" data-testid="profile-card-member" @click="openPersonCard(m.id)">
              <UserAvatar :name="m.name" :github="m.github" :avatar-url="m.avatar_url" />
              <span class="min-w-0 truncate">{{ m.name }}</span>
              <span v-if="m.is_leader" class="pill accent">{{ pick('Captain', '队长') }}</span>
              <span class="card-more">{{ pick('Card', '名片') }} →</span>
            </button>
          </li>
        </ul>
      </template>
    </div>
  </dialog>
</template>

<style scoped>
.card-dialog { width: min(28rem, calc(100vw - 1.5rem)); max-height: calc(100vh - 2rem); padding: 0; margin: auto; border: 1px solid rgba(158,173,255,.35);
  background: #0b1022; color: #e8ecf8; box-shadow: 0 24px 80px rgba(0,0,0,.6); }
.card-dialog::backdrop { background: rgba(2,5,14,.72); backdrop-filter: blur(2px); }
.card-panel { position: relative; padding: 1.4rem 1.4rem 1.2rem; }
.card-close { position: absolute; top: .5rem; right: .6rem; width: 2.2rem; height: 2.2rem; font-size: 1.5rem; line-height: 1; color: #aeb6c8; background: none; border: 0; cursor: pointer; }
.card-back { margin: -.4rem 0 .5rem; border: 0; background: none; color: #9eadff; font-size: .8rem; cursor: pointer; padding: 0; }
.card-head { display: flex; align-items: center; gap: .85rem; margin-top: .9rem; }
.card-head :deep(.user-avatar) { width: 3.2rem; height: 3.2rem; flex: none; }
.card-name { margin: 0 2rem 0 0; font-size: 1.2rem; font-weight: 600; overflow-wrap: anywhere; }
.card-meta { color: rgba(205,214,238,.7); font-size: .85rem; overflow-wrap: anywhere; }
.card-blurb { margin-top: .9rem; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.card-fields { display: grid; grid-template-columns: auto 1fr; gap: .35rem .9rem; margin-top: .9rem; font-size: .9rem; }
.card-fields dt { color: rgba(205,214,238,.6); }
.card-fields dd { margin: 0; overflow-wrap: anywhere; }
.card-actions { display: flex; flex-wrap: wrap; gap: .6rem; align-items: center; margin-top: 1.1rem; padding-top: .9rem; border-top: 1px solid rgba(255,255,255,.1); }
.card-members { list-style: none; margin: .5rem 0 0; padding: 0; }
.card-member { display: flex; width: 100%; align-items: center; gap: .6rem; padding: .55rem 0; border: 0; border-bottom: 1px solid rgba(255,255,255,.08);
  background: none; color: inherit; text-align: left; cursor: pointer; }
.card-member :deep(.user-avatar) { width: 2rem; height: 2rem; flex: none; }
.card-member:hover .card-more { color: #c4ceff; }
.card-more { margin-left: auto; color: #9eadff; font-size: .8rem; white-space: nowrap; }
</style>
