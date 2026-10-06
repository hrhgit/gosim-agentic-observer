<script setup lang="ts">
import UserAvatar from '../components/UserAvatar.vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { supabase } from '../lib/supabase'
import { describeError } from '../lib/errors'
import { useAuth } from '../stores/auth'
import { useFlash } from '../stores/flash'
import DashShell from '../components/layout/DashShell.vue'
import TierBadge from '../components/TierBadge.vue'
import TeamDirectory from '../components/TeamDirectory.vue'
import TeamInbox from '../components/TeamInbox.vue'
import SoloTeamButton from '../components/SoloTeamButton.vue'
import { useTeamCapacity } from '../composables/useTeamCapacity'
import { showsTeamPlaces, teamCreationBlocked } from '../lib/teamCapacity'
import { invalidateBoardCache } from '../lib/data'
import { parseUid } from '../lib/friends'
import { inviteToTeamByUid } from '../lib/friendsApi'

interface Member { id: string; name: string; github: string | null; affiliation: string | null; is_leader: boolean; astro_level: number; ai_level: number; avatar_url: string | null }

const { t, tf } = useI18n()
const i18n = useI18n()
const flash = useFlash()
const { me, team, refreshMe } = useAuth()
const route = useRoute()
const router = useRouter()
const members = ref<Member[]>([])
const busy = ref(false)
const loading = ref(true)
const copied = ref(false)
const linkCopied = ref(false)
const createForm = ref({ name: '', max_size: 3, github_repo: '', project_idea: '' })
const joinForm = ref({ code: '' })
const inviteUid = ref('')
watch(() => route.query.invite, value => {
  if (typeof value === 'string' && /^[A-Za-z0-9]{4,64}$/.test(value)) joinForm.value.code = value.toUpperCase()
}, { immediate: true })
const inviteLink = computed(() => team.value
  ? new URL(router.resolve({ path: '/team', query: { invite: team.value.invite_code } }).href, window.location.origin).href : '')
const editForm = ref({ name: '', max_size: 3, github_repo: '', project_idea: '', is_locked: false })
const isLeader = computed(() => Boolean(team.value && me.value && team.value.leader_id === me.value.id))

const errorText = (e: unknown) => describeError(e, i18n, ['team.errors', 'friends.errors', 'team'])
const { capacity, reload: reloadCapacity } = useTeamCapacity()
const creationBlocked = computed(() => teamCreationBlocked(capacity.value, me.value?.is_admin))

async function load() {
  loading.value = true
  try {
    await refreshMe()
    if (team.value) {
      const { data, error } = await supabase.rpc('team_members', { p_team_id: team.value.id })
      if (error) throw error
      members.value = (data ?? []) as Member[]
      editForm.value = { name: team.value.name, max_size: team.value.max_size, github_repo: team.value.github_repo ?? '', project_idea: team.value.project_idea ?? '', is_locked: team.value.is_locked }
    }
  } catch (e) { flash.error(errorText(e)) }
  finally {
    loading.value = false
    await nextTick()
    const section = joinForm.value.code && !team.value ? 'join' : route.hash.slice(1)
    if (['create', 'join', 'invite'].includes(section)) document.getElementById(section)?.scrollIntoView({ block: 'center' })
  }
}

async function run(action: () => Promise<unknown>, success?: string) {
  busy.value = true
  try {
    await action()
    if (success) flash.success(success)
    await load()
  } catch (e) { flash.error(errorText(e)) }
  finally { busy.value = false }
}
const rpc = async (name: string, args?: Record<string, unknown>) => {
  const { data, error } = await supabase.rpc(name, args)
  if (error) throw error
  return data
}

const createTeam = () => run(async () => {
  try {
    await rpc('create_team', { p_name: createForm.value.name.trim(), p_max_size: Number(createForm.value.max_size), p_project_idea: createForm.value.project_idea.trim(), p_github_repo: createForm.value.github_repo.trim() })
  } finally { void reloadCapacity() }
}, t('flash.team_created'))
const joinTeam = () => run(async () => {
  await rpc('join_team', { p_invite_code: joinForm.value.code.trim().toUpperCase() })
  const fresh = await refreshMe()
  flash.success(tf('flash.team_joined', { name: fresh?.team?.name ?? '' }))
})
const saveTeam = () => run(async () => {
  await rpc('update_team', {
    p_project_idea: editForm.value.project_idea.trim(), p_github_repo: editForm.value.github_repo.trim(),
    p_max_size: Number(editForm.value.max_size), p_is_locked: editForm.value.is_locked,
    p_name: team.value && editForm.value.name.trim() !== team.value.name ? editForm.value.name.trim() : null,
  })
  invalidateBoardCache()
}, t('flash.team_saved'))
const inviteByUid = () => {
  const uid = parseUid(inviteUid.value)
  if (!uid) { flash.error(t('friends.invalid_uid')); return }
  void run(async () => { await inviteToTeamByUid(uid); inviteUid.value = '' }, t('friends.invited'))
}
const regenerate = () => run(() => rpc('regenerate_invite_code'), t('team.code_regenerated'))
const transfer = (id: string) => { if (window.confirm(t('team.transfer_confirm'))) void run(() => rpc('transfer_leadership', { p_user_id: id }), t('flash.team_saved')) }
const kick = (id: string) => { if (window.confirm(t('team.kick_confirm'))) void run(() => rpc('remove_member', { p_user_id: id }), t('flash.team_saved')) }
const leave = () => { if (window.confirm(t('team.leave_confirm'))) void run(() => rpc('leave_team'), t('flash.team_left')) }
const disband = () => { if (window.confirm(t('team.disband_confirm'))) void run(() => rpc('disband_team'), t('flash.team_disbanded')) }

async function copyCode() {
  if (!team.value) return
  try { await navigator.clipboard.writeText(team.value.invite_code); copied.value = true; window.setTimeout(() => { copied.value = false }, 2000) }
  catch { flash.error(t('team.copy_fallback')) }
}
async function copyLink() {
  if (!inviteLink.value) return
  try { await navigator.clipboard.writeText(inviteLink.value); linkCopied.value = true; window.setTimeout(() => { linkCopied.value = false }, 2000) }
  catch { flash.error(t('team.copy_fallback')) }
}

onMounted(load)
</script>

<template>
  <DashShell :kicker="t('dash.title')" :title="t('team.title')">
    <TeamInbox @changed="load" />
    <p v-if="loading" class="text3 text-sm">{{ t('common.loading') }}</p>

    <div v-else-if="team" class="dash-grid">
      <div class="panel">
        <div class="hd"><h2>{{ team.name }}</h2><span class="label">{{ members.length }} / {{ team.max_size }}<template v-if="team.is_locked"> · {{ t('team.locked') }}</template></span></div>
        <p class="text2 text-sm mb-5">{{ t('team.manage_hint') }}</p>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>{{ t('common.name') }}</th><th>{{ t('auth.github') }}</th><th>{{ t('auth.affiliation') }}</th><th></th></tr></thead>
            <tbody>
              <tr v-for="m in members" :key="m.id">
                <td><UserAvatar :name="m.name" :github="m.github" :avatar-url="m.avatar_url" /> {{ m.name }} <span v-if="m.is_leader" class="pill accent ml-1">{{ t('team.leader') }}</span>
                  <span class="wall-badges wall-badges-inline"><TierBadge kind="astro" :level="m.astro_level" /><TierBadge kind="ai" :level="m.ai_level" /></span>
                </td>
                <td class="m text-sm">{{ m.github || '—' }}</td>
                <td class="text-sm">{{ m.affiliation || '—' }}</td>
                <td class="r">
                  <div v-if="isLeader && m.id !== me?.id" class="actions-inline justify-end">
                    <button type="button" class="copy-btn" :disabled="busy" @click="transfer(m.id)">{{ t('team.transfer') }}</button>
                    <button type="button" class="copy-btn" :disabled="busy" @click="kick(m.id)">{{ t('team.kick') }}</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <template v-if="isLeader">
          <div class="hd mt-10"><h3>{{ t('common.save') }}</h3></div>
          <form @submit.prevent="saveTeam">
            <div class="grid-form">
              <label class="field"><span>{{ t('team.name') }}</span><input data-testid="team-name-edit" v-model="editForm.name" type="text" required minlength="2" maxlength="60"></label>
              <label class="field"><span>{{ t('team.max_size') }}</span><input v-model.number="editForm.max_size" type="number" :min="members.length" max="3"></label>
              <label class="field"><span>{{ t('team.github_repo') }}</span><input v-model="editForm.github_repo" type="text"></label>
              <label class="field full"><span>{{ t('team.project_idea') }}</span><textarea v-model="editForm.project_idea"></textarea></label>
            </div>
            <label class="check"><input v-model="editForm.is_locked" type="checkbox"> {{ t('team.locked') }}</label>
            <button class="btn primary sm" type="submit" :disabled="busy">{{ t('common.save') }}</button>
          </form>
        </template>
        <dl v-else class="kv mt-8">
          <dt>{{ t('team.github_repo') }}</dt><dd>{{ team.github_repo || '—' }}</dd>
          <dt>{{ t('team.project_idea') }}</dt><dd class="whitespace-pre-line">{{ team.project_idea || '—' }}</dd>
        </dl>
      </div>

      <div>
        <div id="invite" class="panel">
          <div class="hd"><h2>{{ t('team.invite_code') }}</h2></div>
          <p class="text2 text-sm">{{ t('team.invite_lede') }}</p>
          <div class="token mt-4 text-[1.4rem] tracking-[.2em]" data-testid="team-invite-code">{{ team.invite_code }}</div>
          <label class="field mt-4"><span>{{ t('team.invite_link') }}</span><input :value="inviteLink" readonly data-testid="team-invite-link" @focus="($event.target as HTMLInputElement).select()"></label>
          <div class="actions-inline mt-4">
            <button type="button" class="copy-btn" @click="copyCode">{{ copied ? t('common.copied') : t('common.copy') }}</button>
            <button type="button" class="copy-btn" @click="copyLink">{{ linkCopied ? t('common.copied') : t('team.copy_link') }}</button>
            <button v-if="isLeader" type="button" class="copy-btn" :disabled="busy" @click="regenerate">{{ t('team.regenerate') }}</button>
          </div>
        </div>
        <div v-if="isLeader" id="invite-uid" class="panel mt-8" data-testid="team-invite-uid">
          <div class="hd"><h2>{{ t('friends.invite_uid_title') }}</h2></div>
          <p class="text2 text-sm">{{ t('friends.invite_uid_lede') }}</p>
          <form class="invite-uid-form mt-4" @submit.prevent="inviteByUid">
            <label class="field"><span>UID</span><input v-model="inviteUid" data-testid="team-invite-uid-input" type="text" inputmode="numeric" autocomplete="off" maxlength="20" class="mono" :placeholder="t('friends.add_placeholder')"></label>
            <button class="btn sm" type="submit" data-testid="team-invite-uid-send" :disabled="busy || !inviteUid.trim()">{{ t('friends.invite_uid_send') }}</button>
          </form>
        </div>
        <div class="panel mt-8">
          <div class="hd"><h2>{{ t('common.actions') }}</h2></div>
          <div class="actions-inline">
            <button type="button" class="btn sm" :disabled="busy" @click="leave">{{ t('team.leave') }}</button>
            <button v-if="isLeader" type="button" class="btn sm danger" :disabled="busy" @click="disband">{{ t('team.disband') }}</button>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="dash-grid">
      <div id="create" class="panel">
        <div class="hd"><h2>{{ t('team.create_title') }}</h2></div>
        <div v-if="capacity?.full" class="errors mb-6" role="status" data-testid="team-capacity-full">{{ tf('team.capacity.full', { limit: capacity.limit }) }}</div>
        <p v-else-if="capacity && showsTeamPlaces(capacity)" class="text3 text-sm mb-4" data-testid="team-capacity">{{ tf('team.capacity.remaining', { remaining: capacity.remaining, limit: capacity.limit }) }}</p>
        <div class="solo-callout mb-6" data-testid="solo-callout">
          <p class="text2 text-sm">{{ t('team.solo_lede') }}</p>
          <SoloTeamButton class="mt-3" />
        </div>
        <form @submit.prevent="createTeam">
          <div class="grid-form">
            <label class="field"><span>{{ t('team.name') }}</span><input data-testid="team-name-input" v-model="createForm.name" type="text" required minlength="2" maxlength="60"></label>
            <label class="field"><span>{{ t('team.max_size') }}</span><input v-model.number="createForm.max_size" type="number" min="1" max="3"></label>
            <label class="field"><span>{{ t('team.github_repo') }}</span><input v-model="createForm.github_repo" type="text"></label>
            <label class="field"><span>{{ t('team.project_idea') }}</span><input v-model="createForm.project_idea" type="text"></label>
          </div>
          <button data-testid="team-create" class="btn primary sm" type="submit" :disabled="busy || creationBlocked">{{ t('team.create') }} →</button>
        </form>
      </div>
      <div>
        <div id="join" class="panel">
          <div class="hd"><h2>{{ t('team.join_title') }}</h2></div>
          <form @submit.prevent="joinTeam">
            <label class="field"><span>{{ t('team.invite_code') }}</span><input data-testid="team-join-code" v-model="joinForm.code" type="text" required class="mono uppercase tracking-[.15em]" autocomplete="off"></label>
            <button data-testid="team-join" class="btn sm" type="submit" :disabled="busy">{{ t('team.join') }} →</button>
          </form>
        </div>
        <TeamDirectory />
      </div>
    </div>
  </DashShell>
</template>

<style scoped>
.invite-uid-form { display: flex; flex-wrap: wrap; align-items: flex-end; gap: .75rem; }
.invite-uid-form .field { flex: 1 1 10rem; margin-bottom: 0; }
</style>
