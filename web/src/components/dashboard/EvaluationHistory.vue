<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { supabase } from '../../lib/supabase'
import { competition } from '../../stores/competition'
import { useAuth } from '../../stores/auth'
import { useI18n } from '../../composables/useI18n'
import { num } from '../../lib/format'
import { formatDateTime } from '../../lib/projectText'
import { useQuestFlags } from '../../composables/useQuestFlags'
import { scenarioLabel, scenarioOrder } from '../../lib/scenarioLabels'
// `quiet` keeps the new-submission button secondary while the dashboard quest leads.
// `allPhases` is the records page: every complete-project evaluation with its scenario scores.
const props=withDefaults(defineProps<{limit?:number;quiet?:boolean;allPhases?:boolean;hideEmpty?:boolean}>(),{limit:50,quiet:false,allPhases:false,hideEmpty:false})
const {team}=useAuth(), {pick,t,locale}=useI18n(), {remember}=useQuestFlags()
type Run={id:string;status:string;score:number|null;scenarios?:{slug:string;name:string}|null}
type Batch={id:string;status:string;score:number|null;created_at:string;quota_refunded?:boolean
  phases?:{name_en:string;name_zh:string}|null;observer_runs?:Run[]}
const rows=ref<Batch[]>([]),loading=ref(true),error=ref(false)
let timer:number|undefined
const statuses=computed(()=>pick<Record<string,string>>({queued:'Queued',starting:'Starting',running:'Running',awaiting_csv:'Waiting for CSV',scored:'Scored',failed:'Failed',cancelled:'Cancelled'}, {queued:'排队中',starting:'启动中',running:'运行中',awaiting_csv:'等待 CSV',scored:'已评分',failed:'失败',cancelled:'已取消'}))
const runs=(b:Batch)=>[...(b.observer_runs??[])].sort((x,y)=>scenarioOrder(x.scenarios?.slug??'')-scenarioOrder(y.scenarios?.slug??''))
async function load(){
  if(!team.value || (!props.allPhases && !competition.phaseId)){loading.value=false;return}
  let request=supabase.from('observer_batches')
    .select(props.allPhases?'*,phases(name_en,name_zh),observer_runs(id,status,score,scenarios(slug,name))':'*')
    .eq('team_id',team.value.id).eq('purpose','formal')
  if(!props.allPhases)request=request.eq('phase_id',competition.phaseId!)
  const result=await request.order('created_at',{ascending:false}).limit(props.limit)
  error.value=Boolean(result.error)
  if(!result.error)rows.value=(result.data??[]) as unknown as Batch[]
  loading.value=false
}
onMounted(()=>{void load();timer=window.setInterval(()=>{if(!document.hidden)void load()},15000)})
onUnmounted(()=>window.clearInterval(timer))
</script>
<template>
  <section v-if="!(props.hideEmpty && !error && !rows.length)" class="panel mb-6" data-testid="evaluation-history">
    <div class="hd"><h2>{{ props.allPhases ? pick('Complete-project evaluations','完整项目评测记录') : pick('Evaluations','评测记录') }}</h2><button class="btn sm" @click="load">{{ pick('Refresh','刷新') }}</button></div>
    <p v-if="props.allPhases" class="help">{{ pick('Each evaluation runs every scenario of its phase once; its score is the average. Evaluations that failed because of the platform are not counted toward the daily limit.','每次评测把该赛程全部场景各跑一遍，分数是这些场景的平均分；因平台原因失败的评测不计入每日次数。') }}</p>
    <p v-if="loading">{{ t('common.loading') }}</p>
    <p v-else-if="error" role="alert">{{ pick('Could not load evaluations. Please refresh.','无法加载评测记录，请刷新重试。') }}</p>
    <p v-else-if="!rows.length" class="text2">{{ pick('No evaluations yet.','还没有评测记录。') }}</p>
    <div v-else class="table-wrap"><table class="data-table">
      <thead><tr><th>{{ t('subs.when') }}</th><th v-if="props.allPhases">{{ t('subs.phase') }}</th><th>{{ t('common.status') }}</th><th>{{ props.allPhases ? pick('Average score','平均分') : t('subs.score') }}</th><th v-if="props.allPhases">{{ pick('Scenario scores','各场景得分') }}</th><th>{{ pick('Details','详情') }}</th></tr></thead>
      <tbody><tr v-for="row in rows" :key="row.id" :data-batch-id="row.id">
        <td class="m xs whitespace-nowrap">{{ formatDateTime(row.created_at, locale) }}</td>
        <td v-if="props.allPhases">{{ row.phases ? pick(row.phases.name_en,row.phases.name_zh) : '—' }}</td>
        <td>{{ statuses[row.status]??row.status }}<span v-if="row.quota_refunded" class="pill info ml-2" data-testid="batch-refunded">{{ pick('Not counted toward the daily limit','未计入次数') }}</span></td>
        <td class="m">{{ num(row.score) }}</td>
        <td v-if="props.allPhases" class="m xs"><span v-for="(run,index) in runs(row)" :key="run.id" class="mr-3 inline-block">{{ run.scenarios ? scenarioLabel(run.scenarios.slug, run.scenarios.name, locale) : pick(`Scenario ${index+1}`,`场景 ${index+1}`) }}: {{ run.score!=null ? num(run.score) : statuses[run.status]??run.status }}</span></td>
        <td><router-link class="accent-l" :to="'/compete?track=project#batch-'+row.id" @click="remember('review','competition')">{{ pick('View progress and results','查看进度与结果') }}</router-link></td>
      </tr></tbody>
    </table></div>
    <p v-if="!props.allPhases" class="mt-5"><router-link class="btn sm" :class="{ primary: !props.quiet }" to="/compete">{{ t('dash.new_submission') }} →</router-link></p>
  </section>
</template>
