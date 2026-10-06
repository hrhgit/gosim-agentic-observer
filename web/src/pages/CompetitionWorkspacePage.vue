<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { loadCompeteUiSetting, loadPhases,type Phase } from '../lib/data'
import { COMPETE_UI_STORAGE, competeLayout, layoutOverride, type CompeteLayout } from '../lib/competeUi'
import { chooseEntry, competition, entryChoice, loadCompetition, useEntryFor } from '../stores/competition'
import { offersPracticeSwitch } from '../lib/entryPhase'
import { useAuth } from '../stores/auth'
import { useI18n } from '../composables/useI18n'
import DashShell from '../components/layout/DashShell.vue'
import CompeteGuide from '../components/competition/CompeteGuide.vue'
import ProjectWorkflow from '../components/competition/ProjectWorkflow.vue'
import SoloTeamButton from '../components/SoloTeamButton.vue'
import TeamInbox from '../components/TeamInbox.vue'
const {t,pick}=useI18n(),{team,me,refreshMe}=useAuth()
const phase=ref<Phase|null>(null),allPhases=ref<Phase[]>([]),loading=ref(true),failed=ref(false),layout=ref<CompeteLayout>('classic')
// The header names the phase this page evaluates in: the workflow reports the one it binds to; until then the
// first phase that has not ended, in the workflow's order (beta entry, complete-project board, global phase).
const kickerPhase=ref<Phase|null>(null),workflowPhase=ref<{name_en:string;name_zh:string}|null>(null)
const kicker=computed(()=>{
  if(onlineEndedView.value&&phase.value)return pick(phase.value.name_en+' · Ended',phase.value.name_zh+' · 已截止')
  const p=workflowPhase.value??kickerPhase.value??phase.value;return p?pick(p.name_en,p.name_zh):''})
// Simplified layout behind a switch (site_settings 'event'.compete_ui); ?ui=v2|v1|auto overrides it for this browser.
async function chooseLayout(){
  const query=new URLSearchParams(location.search).get('ui')
  let remembered:string|null=null
  try{
    if(query==='auto')localStorage.removeItem(COMPETE_UI_STORAGE)
    else if(query==='v2'||query==='v1')localStorage.setItem(COMPETE_UI_STORAGE,query)
    remembered=localStorage.getItem(COMPETE_UI_STORAGE)
  }catch{/* storage blocked: the switch alone decides */}
  const override=layoutOverride(query,remembered)
  layout.value=competeLayout(override?{v2_all:false,v2_teams:[]}:await loadCompeteUiSetting(),team.value?.id,override)
}
// During the competition practice stays open: 线上赛 by default, 练习赛 one click away (remembered per user).
const showSwitch=computed(()=>offersPracticeSwitch(competition))
// After the online deadline the page opens on practice; online stays one click away, read-only.
const onlineEnded=computed(()=>{const p=allPhases.value.find(x=>x.id===competition.phaseId);return !!p?.ends_at&&Date.parse(p.ends_at)<=Date.now()})
const onlineEndedView=computed(()=>showSwitch.value&&onlineEnded.value&&entryChoice.value==='online')
const boardSlug=computed(()=>entryChoice.value==='practice'?allPhases.value.find(p=>p.id===competition.practicePhaseId)?.slug??'practice-projects':'online')
onMounted(async()=>{try{await refreshMe()
  await chooseLayout()
  // Team or membership changes do not raise auth events, so the workspace always
  // re-resolves the beta entry fresh instead of trusting the 15s cache.
  await loadCompetition(true)
  // Public pages keep the single global phase; this workspace alone swaps in the
  // team-restricted beta entry for its access team.
  const phases=await loadPhases(true)
  allPhases.value=phases
  useEntryFor(me.value?.id,onlineEnded.value)
  const global=phases.find(p=>competition.phaseId?p.id===competition.phaseId:p.slug===(competition.mode==='practice'?'practice':'online'))
  phase.value=phases.find(p=>p.id===competition.betaPhaseId)??global??null
  const open=(p:Phase|undefined)=>!!p&&p.is_active!==false&&(!p.ends_at||Date.parse(p.ends_at)>Date.now())
  kickerPhase.value=[competition.betaPhaseId,competition.projectPhaseId,global?.id].map(id=>phases.find(p=>p.id===id)).find(open)??phase.value
}catch{failed.value=true}finally{loading.value=false}})
</script>
<template>
  <DashShell :kicker="kicker" :title="pick('Participate','参赛')">
    <!-- Join requests and invitations waiting for an answer: shown here too so a captain cannot miss them. -->
    <TeamInbox compact class="mt-6" @changed="refreshMe" />
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <p v-else-if="failed" role="alert">{{ pick('Could not load the competition. Please refresh.','比赛信息加载失败，请刷新重试。') }}</p>
    <div v-else-if="!team" class="panel"><p>{{ t('submit.errors.need_team') }}</p><p class="mt-5 actions-inline"><router-link class="btn primary sm" to="/team">{{ t('nav.team') }} →</router-link><SoloTeamButton /></p></div>
    <p v-else-if="!phase" class="panel">{{ pick('No competition is available yet.','当前还没有开放的比赛。') }}</p>
    <template v-else>
      <!-- 新手指南: first-visit spotlight tour of the simplified layout, replayable from its button. -->
      <div class="compete-top mb-5">
      <div v-if="showSwitch" class="actions-inline" role="group" :aria-label="pick('Evaluation phase','评测赛程')" data-testid="entry-switch">
        <button type="button" class="btn sm" :class="{ primary: entryChoice==='online' }" :aria-pressed="entryChoice==='online'" data-testid="entry-online" @click="chooseEntry('online')">{{ onlineEnded ? pick('Online competition · Ended','线上赛 · 已截止') : pick('Online competition','线上赛') }}</button>
        <button type="button" class="btn sm" :class="{ primary: entryChoice==='practice' }" :aria-pressed="entryChoice==='practice'" data-testid="entry-practice" @click="chooseEntry('practice')">{{ pick('Practice','练习赛') }}</button>
        <router-link class="text2 text-sm" :to="`/leaderboard/${boardSlug}`" data-testid="entry-board">{{ entryChoice==='practice' ? pick('Practice leaderboard →','练习赛排行榜 →') : pick('Online leaderboard →','线上赛排行榜 →') }}</router-link>
        <span class="text3 text-sm" data-testid="entry-note">{{ onlineEndedView ? pick('The online phase has ended: its records, logs and downloads stay available and the final version is locked. Switch to Practice to keep evaluating.','线上赛已截止：评测记录、日志和下载仍可查看，最终版本已锁定。继续评测请切换到练习赛。') : entryChoice==='practice' ? pick('Practice does not affect the online ranking; it has its own daily evaluations.','练习赛不影响线上赛排名，评测次数单独计算。') : pick('Evaluations here count for the online leaderboard.','这里的评测计入线上赛排行榜。') }}</span>
      </div>
      <div v-if="layout==='v2'" class="compete-guide-btn"><CompeteGuide /></div>
      </div>
      <ProjectWorkflow :layout="layout" @phase="p => workflowPhase = p" />
    </template>
  </DashShell>
</template>

<style scoped>
.compete-top { display: flex; flex-wrap: wrap; align-items: flex-start; gap: .75rem; }
.compete-top > [data-testid="entry-switch"] { flex: 1 1 18rem; }
.compete-guide-btn { margin-left: auto; }
</style>
