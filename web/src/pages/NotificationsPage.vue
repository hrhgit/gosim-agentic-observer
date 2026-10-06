<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import DashShell from '../components/layout/DashShell.vue'
import { useI18n } from '../composables/useI18n'
import { describeError } from '../lib/errors'
import { useAuth } from '../stores/auth'
import { teamAction } from '../stores/teamNotifications'
import { openPersonCard, openTeamCard } from '../stores/profileCard'
const i18n = useI18n(), { pick, t } = i18n
const { refreshMe } = useAuth()
type Invitation = { id:string; kind:'invite'|'request'; direction:'sent'|'received'; team_id:string|null; sender_id:string; recipient_id:string; team_name:string; sender_name:string; recipient_name:string; status:string; updated_at:string; unread:boolean }
const rows=ref<Invitation[]>([]), loading=ref(true), busy=ref(''), error=ref(''), more=ref(false), fetching=ref(false)
const statuses=computed(() => pick<Record<string,string>>({pending:'Waiting for a response',accepted:'Accepted',declined:'Declined',cancelled:'Cancelled'}, {pending:'等待处理',accepted:'已接受',declined:'已拒绝',cancelled:'已撤回'}))
async function reload(append=false) {
  if(fetching.value) return
  fetching.value=true;error.value=''
  try {
    const last=append?rows.value.at(-1):null
    const received:Invitation[]=await teamAction('my_team_invitations',last?{p_before:last.updated_at,p_before_id:last.id}:{})
    more.value=received.length===100
    rows.value=append?[...rows.value,...received.filter(r=>!rows.value.some(old=>old.id===r.id))]:received
    if (received.length) await teamAction('mark_team_invitations_read', {p_ids:received.map(r=>r.id),p_through:received.map(r=>r.updated_at).sort().at(-1)})
  } catch(e) { error.value=describeError(e,i18n,['team.errors','team']) }
  finally { loading.value=false;fetching.value=false }
}
async function respond(row:Invitation,accept:boolean) {
  if (busy.value) return
  busy.value=row.id; error.value=''
  try { await teamAction('respond_team_invite',{p_invitation:row.id,p_accept:accept}); await refreshMe(); await reload() }
  catch(e) { error.value=describeError(e,i18n,['team.errors','team']) }
  finally { busy.value='' }
}
async function cancel(row:Invitation) {
  if (busy.value) return
  busy.value=row.id; error.value=''
  try { await teamAction('cancel_team_invite',{p_invitation:row.id}); await reload() }
  catch(e) { error.value=describeError(e,i18n,['team.errors','team']) }
  finally { busy.value='' }
}
onMounted(()=>reload())
</script>

<template>
  <DashShell :kicker="t('dash.title')" :title="pick('Team notifications','组队通知')">
    <p class="text2 mb-5">{{ pick('Track requests and invitations here. Membership changes only after acceptance.', '在这里处理加入申请和队伍邀请、查看进度；接受后才会改变队伍成员。') }}</p>
    <button class="btn sm mb-5" :disabled="!!busy || fetching" @click="reload()">{{ pick('Refresh','刷新') }}</button>
    <p v-if="error" role="alert" class="errors">{{ error }}</p>
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <template v-else>
      <section v-for="direction in (['received','sent'] as const)" :key="direction" class="panel mb-6" :data-testid="'notifications-'+direction">
        <div class="hd"><h2>{{ direction==='received' ? pick('Received','收到的') : pick('Sent','发出的') }}</h2></div>
        <p v-if="!rows.some(r=>r.direction===direction)" class="text3">{{ pick('Nothing here yet.','暂无记录。') }}</p>
        <article v-for="row in rows.filter(r=>r.direction===direction)" :key="row.id" class="border-b border-border-subtle py-4" :data-invitation-id="row.id">
          <h3><button v-if="row.status==='pending' && row.team_id" type="button" class="card-link" data-testid="notification-open-team" @click="openTeamCard(row.team_id)">{{ row.team_name }}</button><template v-else>{{ row.team_name }}</template> · {{ row.kind==='invite' ? pick('Team invitation','队伍邀请') : pick('Request to join','加入申请') }}</h3>
          <p class="text2 text-sm mt-2"><button v-if="row.status==='pending'" type="button" class="card-link" data-testid="notification-open-person" @click="openPersonCard(direction==='received' ? row.sender_id : row.recipient_id)">{{ direction==='received' ? row.sender_name : row.recipient_name }}</button><template v-else>{{ direction==='received' ? row.sender_name : row.recipient_name }}</template> · <strong>{{ statuses[row.status] }}</strong></p>
          <div v-if="row.status==='pending'" class="actions-inline mt-3">
            <template v-if="direction==='received'">
              <button class="btn primary sm" :disabled="!!busy" @click="respond(row,true)">{{ pick('Accept','接受') }}</button>
              <button class="btn sm" :disabled="!!busy" @click="respond(row,false)">{{ pick('Decline','拒绝') }}</button>
            </template>
            <button v-else class="btn sm" :disabled="!!busy" @click="cancel(row)">{{ pick('Withdraw','撤回') }}</button>
          </div>
          <router-link v-else-if="row.status==='accepted'" class="copy-btn inline-block mt-3" to="/team">{{ t('nav.team') }} →</router-link>
        </article>
      </section>
      <button v-if="more" class="btn sm" :disabled="fetching || !!busy" @click="reload(true)">{{ pick('Load older notifications','查看更早的通知') }}</button>
    </template>
  </DashShell>
</template>

<style scoped>
.card-link { padding: 0; border: 0; background: none; color: inherit; font: inherit; cursor: pointer; text-decoration: underline dotted rgba(158,173,255,.7); text-underline-offset: 4px; }
.card-link:hover { color: #c4ceff; }
</style>
