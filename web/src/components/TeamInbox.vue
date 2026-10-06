<script setup lang="ts">
// Requests and invitations waiting for the person's answer, plus their own requests' progress. Shown at the top of
// the team page and the dashboard so a waiting request cannot be missed (it stays until answered, read or not).
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { describeError } from '../lib/errors'
import { fmtUtc } from '../lib/format'
import { supabase } from '../lib/supabase'
import { blockedText, normalizeInbox, type InboxRow, type TeamInbox } from '../lib/teamInbox'
import { useAuth } from '../stores/auth'
import { pendingTeamActions, teamAction } from '../stores/teamNotifications'
import { openPersonCard, openTeamCard } from '../stores/profileCard'

const props = defineProps<{ compact?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const i18n = useI18n(), { pick } = i18n
const { refreshMe, isLoggedIn } = useAuth()
const route = useRoute()
const inbox = ref<TeamInbox>({ received: [], sent: [] }), busy = ref(''), error = ref(''), loaded = ref(false)
const requests = computed(() => inbox.value.received.filter(r => r.kind === 'request'))
const invites = computed(() => inbox.value.received.filter(r => r.kind === 'invite'))
// The dashboard only shows what needs an answer; the team page also shows the person's own sent items.
const sent = computed(() => props.compact ? [] : inbox.value.sent)
const statuses = computed(() => pick<Record<string, string>>(
  { pending: 'Waiting for the captain', accepted: 'Accepted', declined: 'Declined', cancelled: 'Withdrawn' },
  { pending: '等待对方处理', accepted: '已通过', declined: '已拒绝', cancelled: '已撤回' }))

async function load() {
  if (!isLoggedIn.value) return
  const { data, error: e } = await supabase.rpc('my_team_inbox')
  if (!e) inbox.value = normalizeInbox(data)
  if (!loaded.value && route.hash === '#requests') {
    loaded.value = true
    await nextTick()
    document.getElementById('requests')?.scrollIntoView({ block: 'start' })
  }
  loaded.value = true
}
async function respond(row: InboxRow, accept: boolean) {
  if (busy.value) return
  busy.value = row.id; error.value = ''
  try { await teamAction('respond_team_invite', { p_invitation: row.id, p_accept: accept }); await refreshMe(); emit('changed') }
  catch (e) { error.value = describeError(e, i18n, ['team.errors', 'team']) }
  finally { busy.value = ''; await load() }
}
async function withdraw(row: InboxRow) {
  if (busy.value) return
  busy.value = row.id; error.value = ''
  try { await teamAction('cancel_team_invite', { p_invitation: row.id }) }
  catch (e) { error.value = describeError(e, i18n, ['team.errors', 'team']) }
  finally { busy.value = ''; await load() }
}
onMounted(load)
// Badge polling noticed a change: reload so the list matches the badge.
watch(pendingTeamActions, () => { if (loaded.value) void load() })
defineExpose({ load })
</script>

<template>
  <div v-if="loaded && (inbox.received.length || sent.length)" class="mb-8" data-testid="team-inbox">
    <section v-for="group in ([['request', requests], ['invite', invites]] as const)" v-show="group[1].length" :key="group[0]" :id="group[0] === 'request' || !requests.length ? 'requests' : undefined"
      class="panel inbox-panel mb-6" :data-testid="'team-inbox-' + group[0]">
      <div class="hd">
        <h2>{{ group[0] === 'request' ? pick('Pending join requests', '待处理的入队申请') : pick('Team invitations for you', '收到的队伍邀请') }}
          ({{ group[1].filter(r => !r.blocked).length }})</h2>
        <router-link v-if="compact" class="label accent" to="/team#requests">{{ pick('Team page', '队伍页') }} →</router-link>
      </div>
      <p class="text2 text-sm mb-2">{{ group[0] === 'request'
        ? pick('These people asked to join your team from the Teammates wall. Nothing changes until you accept.', '这些人在「找队友」里申请加入你的队伍。你接受之前不会改变队伍成员。')
        : pick('Accepting puts you in that team.', '接受后你会加入该队伍。') }}</p>
      <article v-for="row in group[1]" :key="row.id" class="inbox-row" :class="{ blocked: row.blocked }" :data-invitation-id="row.id">
        <div class="min-w-0">
          <button v-if="group[0] === 'request'" type="button" class="card-link" data-testid="inbox-open-card" :title="pick('View profile card', '查看名片')"
            @click="openPersonCard(row.sender_id)">{{ row.sender_name }}</button>
          <button v-else type="button" class="card-link" data-testid="inbox-open-card" :title="pick('View team card', '查看队伍名片')"
            @click="openTeamCard(row.team_id)">{{ row.team_name }}</button>
          <span class="text3 text-xs ml-2"><template v-if="group[0] === 'invite'">{{ pick('from ', '邀请人 ') }}<button type="button" class="card-link-sm"
            @click="openPersonCard(row.sender_id)">{{ row.sender_name }}</button> · </template>{{ fmtUtc(row.created_at, { short: true }) }}</span>
          <span class="card-hint">{{ pick('Click the name to see their card', '点名字查看名片') }}</span>
          <p v-if="blockedText(row.blocked)" class="text3 text-xs mt-1">{{ pick(blockedText(row.blocked)!.en, blockedText(row.blocked)!.zh) }}</p>
        </div>
        <div class="actions-inline">
          <button v-if="!row.blocked || row.blocked === 'locked' || row.blocked === 'full'" class="btn primary sm" :disabled="!!busy || !!row.blocked"
            data-testid="inbox-accept" @click="respond(row, true)">{{ pick('Accept', '接受') }}</button>
          <button class="btn sm" :disabled="!!busy" data-testid="inbox-decline" @click="respond(row, false)">{{ row.blocked === 'joined_other_team' || row.blocked === 'already_in_team' ? pick('Dismiss', '移除') : pick('Decline', '拒绝') }}</button>
        </div>
      </article>
    </section>

    <section v-if="sent.length" class="panel mb-6" data-testid="team-inbox-sent">
      <div class="hd"><h2>{{ sent.some(r => r.kind === 'request') ? pick('My requests', '我的申请') : pick('Invitations I sent', '我发出的邀请') }}</h2>
        <router-link class="label accent" to="/notifications">{{ pick('All notifications', '全部通知') }} →</router-link></div>
      <article v-for="row in sent" :key="row.id" class="inbox-row" :data-invitation-id="row.id">
        <div class="min-w-0">
          <button v-if="row.status === 'pending'" type="button" class="card-link" data-testid="inbox-open-card"
            @click="row.kind === 'request' ? openTeamCard(row.team_id) : openPersonCard(row.recipient_id)">{{ row.kind === 'request' ? row.team_name : row.recipient_name }}</button>
          <strong v-else>{{ row.kind === 'request' ? row.team_name : row.recipient_name }}</strong>
          <span class="text3 text-xs ml-2">{{ row.kind === 'request' ? pick('Join request', '入队申请') : pick('Invitation to ', '邀请加入 ') + row.team_name }} · {{ fmtUtc(row.updated_at, { short: true }) }}</span>
        </div>
        <div class="actions-inline">
          <span class="pill" :class="{ accent: row.status === 'accepted' }" data-testid="inbox-status">{{ row.kind === 'invite' && row.status === 'pending' ? pick('Waiting for a response', '等待对方处理') : statuses[row.status] ?? row.status }}</span>
          <button v-if="row.status === 'pending'" class="btn sm" :disabled="!!busy" data-testid="inbox-withdraw" @click="withdraw(row)">{{ pick('Withdraw', '撤回') }}</button>
        </div>
      </article>
    </section>
    <p v-if="error" role="alert" class="errors">{{ error }}</p>
  </div>
</template>

<style scoped>
.inbox-panel { border-color: rgba(120,166,255,.55); }
.inbox-row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .75rem; padding: .85rem 0; border-bottom: 1px solid var(--color-border-subtle, rgba(255,255,255,.08)); }
.inbox-row:last-child { border-bottom: 0; }
.inbox-row.blocked strong, .inbox-row.blocked .card-link { opacity: .6; }
.card-link { padding: 0; border: 0; background: none; color: inherit; font-weight: 600; cursor: pointer; text-align: left;
  text-decoration: underline dotted rgba(158,173,255,.7); text-underline-offset: 4px; }
.card-link:hover, .card-link-sm:hover { color: #c4ceff; }
.card-link-sm { padding: 0; border: 0; background: none; color: inherit; font: inherit; cursor: pointer; text-decoration: underline dotted; text-underline-offset: 3px; }
.card-hint { display: block; margin-top: .15rem; font-size: .72rem; color: rgba(205,214,238,.45); }
</style>
