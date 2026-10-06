<script setup lang="ts">
// Under a friend in the friend lists (floating panel and 好友 page): their team's latest rank, and
// 「邀请入队」 (I'm a captain, they have no team) or 「申请加入」 (I have no team, their team can take a request).
import { computed, ref } from 'vue'
import { useI18n } from '../composables/useI18n'
import { useAuth } from '../stores/auth'
import { useFlash } from '../stores/flash'
import { describeError } from '../lib/errors'
import { num } from '../lib/format'
import { rankFor, teamAction, type FriendTeamContext } from '../lib/friendTeams'
import { inviteToTeamByUid } from '../lib/friendsApi'
import { teamAction as runTeamAction } from '../stores/teamNotifications'

const props = defineProps<{ userId: string; uid: number | null; inTeam: boolean; ctx: FriendTeamContext }>()
const { t, tf } = useI18n()
const i18n = useI18n()
const flash = useFlash()
const { me, team } = useAuth()
const done = ref<'invited' | 'requested' | null>(null)
const busy = ref(false)

const action = computed(() => teamAction({
  myTeam: !!team.value, captain: !!team.value && !!me.value && team.value.leader_id === me.value.id,
  friendInTeam: props.inTeam, friendTeam: props.ctx.teams[props.userId], requested: done.value === 'requested',
}))
const rank = computed(() => rankFor(props.ctx, props.userId))
const boardName = computed(() => (props.ctx.board === 'online' ? t('friend_team.board_online') : t('friend_team.board_practice')))

async function run() {
  const a = action.value
  if (!a || busy.value) return
  busy.value = true
  try {
    if (a.kind === 'invite' && props.uid) { await inviteToTeamByUid(props.uid); done.value = 'invited'; flash.success(t('friends.invited')) }
    else if (a.kind === 'apply') { await runTeamAction('request_team_join', { p_team_id: a.team_id }); done.value = 'requested'; flash.success(t('friend_team.applied')) }
  } catch (e) { flash.error(describeError(e, i18n, ['friends.errors', 'team.errors', 'team'])) }
  finally { busy.value = false }
}
</script>

<template>
  <span v-if="rank || action" class="ftl" data-testid="friend-team-line" @click.stop>
    <span v-if="rank === 'none'" class="ftl-rank" data-testid="friend-team-rank">{{ boardName }} · {{ t('friend_team.no_score') }}</span>
    <span v-else-if="rank" class="ftl-rank" data-testid="friend-team-rank">{{ boardName }} · {{ tf('friend_team.rank', { n: rank.rank }) }} · <span class="mono">{{ num(rank.score, 2) }}</span></span>
    <template v-if="action">
      <button v-if="action.kind === 'invite'" type="button" class="copy-btn ftl-btn" data-testid="friend-invite" :disabled="busy || done === 'invited' || !uid" @click="run">
        {{ done === 'invited' ? t('friends.invited_short') : t('friends.invite_team') }}</button>
      <button v-else-if="action.kind === 'apply'" type="button" class="copy-btn ftl-btn" data-testid="friend-apply" :disabled="busy" @click="run">{{ t('friend_team.apply') }}</button>
      <button v-else type="button" class="copy-btn ftl-btn" data-testid="friend-apply" disabled :title="t(`friend_team.reason_${action.reason}`)">
        {{ action.reason === 'requested' ? t('friend_team.requested') : t('friend_team.apply') }}<span v-if="action.reason !== 'requested'" class="ftl-why"> · {{ t(`friend_team.reason_${action.reason}`) }}</span></button>
    </template>
  </span>
</template>

<style scoped>
.ftl { display: flex; flex-wrap: wrap; align-items: center; gap: .3rem .6rem; font-size: .74rem; color: rgba(205, 214, 238, .7); }
.ftl-btn { padding: .2rem .5rem; font-size: .66rem; }
.ftl-why { text-transform: none; letter-spacing: 0; }
</style>
