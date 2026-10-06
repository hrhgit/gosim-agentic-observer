<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { useAuth } from '../../stores/auth'
import { supabase } from '../../lib/supabase'
import { portal, uploadProjectFile, type PortalData, type ProjectRevision } from '../../lib/observerPortal'
import { inspectSourceNames, zipEntryNames, ZipWithoutCodeError } from '../../lib/sourceCheck'
import { triggerDownload } from '../../lib/storage'
import { usePersonalModel } from '../../composables/usePersonalModel'
import TeamEnvironment from './TeamEnvironment.vue'
import KimiPlanPanel from '../dashboard/KimiPlanPanel.vue'
import ApiTokensPanel from '../dashboard/ApiTokensPanel.vue'
import RunLogs from './RunLogs.vue'
import { configuredServices } from '../../lib/modelServices'
import { DEFAULT_MODEL_KEY_MODE, relayMissesHiddenFinal, teamModelMode, type ModelKeyMode,
  DEFAULT_MODEL_PROTOCOL, teamModelProtocol, type ModelProtocol } from '../../lib/modelKeyMode'
import { competition, entryPhase } from '../../stores/competition'
import { entryPhaseIds, offersPracticeSwitch } from '../../lib/entryPhase'
import { tabFromQuery } from '../../lib/deepLink'
import { activeEvaluations, canChooseFinal, evaluateBlock, latestFailure, type EvaluateBlock, canClearFinal, canSelfCheck, canWithdraw, countedEvaluations, evaluationMetadata, evaluationZipName, finalRole, finalVersionFor, isNoModel, preparationQuota, recentDuplicate, repeatSummaries, SELF_CHECK_RUNS, visibleProjects, withdrawnCount } from '../../lib/projectEvaluation'
import { canPrepareAgain, cardFolderName, flattenResultEntries, formatDailyReset, formatDateTime, manifestForDisplay, orderedCardFolder, revisionErrorText } from '../../lib/projectText'
import { bytes } from '../../lib/format'
import { scenarioLabel, scenarioOrder } from '../../lib/scenarioLabels'
/** 'v2' shows the simplified layout (see below); anything else the classic one. */
const props = defineProps<{ layout?: 'classic' | 'v2' }>()
/** The phase evaluations here go to (null when none is open), so the page header can name it. */
const emit = defineEmits<{ phase: [phase: { name_en: string; name_zh: string } | null] }>()
const { pick, t, tf, locale } = useI18n()
const { team, refreshMeCached } = useAuth()
const personal=usePersonalModel()
const data = ref<PortalData | null>(null)
// Suggestions only: any public https:// address works (the server refuses IPs and internal names).
const personalBases=computed(()=>data.value?.model_bases.filter(base=>base.startsWith('https://'))??[])
const loading = ref(true), busy = ref(false), error = ref(''), notice = ref('')
// A completed request keeps its button disabled until the list shows its result,
// so a second click (or a failed refresh) cannot create a duplicate.
const locked = ref(new Set<string>()), showWithdrawn = ref(false), targetBatch = ref('')
// The action a click started, until its request and the refreshed list are back: its button reads "Working…".
const pending = ref('')
const form = ref({ title: '', kind: 'repository', url: '', branch: '', subdir: '' })
const selectedFile = ref<File | null>(null), review = ref<ProjectRevision | null>(null)
/** 0–100 while a ZIP is uploading; null the rest of the time. */
const uploadPercent = ref<number | null>(null)
/** Set while a dropped connection is being retried automatically; cleared once the attempt settles. */
const retryStatus = ref('')
const reviewPanel = ref<HTMLElement | null>(null)
const phaseId = ref(''), confirmed = ref(false), notes = ref(''), codeUrl = ref('')
/** The row whose logs are open under it: 'run:<id>' or 'rev:<id>' (one at a time). */
const openLogs = ref('')
/** Per batch id, progress of a "download all results" bundle in flight; absent once it is not running. */
const zipProgress = ref<Record<string, { done: number; total: number }>>({})
/** Per batch id, the outcome of the last bundle, shown beside its button (the page-level notice is often scrolled out of view). */
const zipOutcome = ref<Record<string, { text: string; failed: boolean }>>({})
// Formal model calls use the team's choice: a key saved encrypted on the server
// (default, deleted automatically after the results are verified) or the relay
// to this open page, where nothing is stored.
const modelMode = computed(() => teamModelMode(data.value?.team_model))
const savedModel = computed(() => data.value?.team_model?.saved ?? null)
const modeChoice = ref<ModelKeyMode>(DEFAULT_MODEL_KEY_MODE), replacingKey = ref(false)
// Which shape the team's own provider speaks; the platform never translates between them.
const protocolChoice = ref<ModelProtocol>(DEFAULT_MODEL_PROTOCOL)
// Collapsed by default; teams that use a model (saved key or a key connected in this tab) always see it open.
const modelOpen = ref(false)
const modelForm = ref({ base_url: '', model: '', key: '' })
// Rollout switch: the team's variables and allowed domains replace the model API settings.
const teamEgress = computed(() => data.value?.team_environment?.enabled === true)
const relayRunning = computed(() => modelMode.value === 'relay' && (data.value?.batches ?? []).some(b => ['queued', 'running'].includes(b.status)))
watch(modelMode, mode => { if (mode === 'stored') personal.clear() })
const when = (value: string | null | undefined) => formatDateTime(value, locale.value)
const sentences = (...parts: string[]) => parts.join(['zh', 'ja'].includes(locale.value) ? '' : ' ')
let timer: ReturnType<typeof setInterval> | undefined
const words = computed(() => pick({
  title: 'Agent projects', intro: 'Submit a complete project, test its interface, then confirm the exact version for evaluation.',
  localInfo: 'Local run instructions', runner: 'Download local runner', credential: 'Temporary run credential', copy: 'Copy credential',
  localCommand: 'Extract the runner, replace the project path, and run this command. Paste the credential at its hidden prompt.',
  expires: 'Credential expires', localSecret: 'This credential is only for this run. Do not commit it to a repository.',
  closed: 'Project evaluation is not open for the current competition.',
  step1: 'Step 1 · Upload a project', step1Note: 'Uploading does not use evaluations.', prepPerDay: 'Uploads per day', prepLeft: 'left today',
  step2: 'Step 2 · Review and confirm a version', step2Note: 'Does not use evaluations. When preparation finishes, open the review, check the execution settings and adapter code, and confirm the version.',
  step3: 'Step 3 · Start an evaluation', step3Note: 'Each click uses one of today’s evaluations. One evaluation runs every scenario of this phase once; its score is the average of those scenarios. The leaderboard keeps your team’s best evaluation. Evaluations that fail because of the platform are not counted.',
  left: 'Evaluations left today', perDay: 'per day', dailyLimit: 'Daily evaluation limit', active: 'Your team can run up to {n} evaluations at a time. Start the next one when one of them finishes.',
  noneLeft: 'No evaluations left today.', noApproved: 'Confirm a version in step 2 first.', evaluated: 'Evaluated', times: '×', confirmedAt: 'Confirmed',
  evaluateAgain: 'Evaluate again', repeat: 'This version has already been evaluated. Evaluating it again uses one more of today’s evaluations', repeatLeft: 'left today', proceed: 'Continue?',
  withdraw: 'Withdraw', withdrawConfirm: 'Withdraw this version? It will be hidden and can no longer be confirmed or evaluated. The upload still counts toward today’s uploads.',
  withdrawn: 'Version withdrawn.', withdrawnPill: 'Withdrawn', showWithdrawn: 'Show withdrawn versions', hideWithdrawn: 'Hide withdrawn versions',
  duplicate: 'You submitted the same project a few minutes ago. Submit it again? This uses one of today’s uploads.',
  logs: 'View logs', refunded: 'Not counted toward the daily limit', noBatches: 'No evaluations yet.',
  prepareAgain: 'Prepare again', prepareAgainConfirm: 'Prepare this repository again from the latest commit of the same branch (default branch if none was named) and folder? This uses one of today’s uploads.',
  reuploadZip: 'Fix the project and upload the ZIP again (the uploaded ZIP is not kept for another attempt).',
  csv: 'Existing CSV submission', newProject: 'Submit a project', name: 'Project name', repository: 'Public GitHub repository',
  branch: 'Branch, tag or commit (optional, default branch if empty)', subdir: 'Project folder in the repository (optional, repository root if empty)',
  repositoryHint: 'Links to a branch or folder also work, e.g. https://github.com/owner/project/tree/dev/agent. The exact commit is saved when you submit.',
  zip: 'Private ZIP upload', privacy: 'Public repositories remain public after forking. ZIP projects and detailed results are private to your team and the organizers.',
  file: 'Complete project ZIP · up to 50 MB', submit: 'Upload and prepare', projects: 'Your projects', empty: 'No projects yet.',
  refresh: 'Refresh', review: 'Review interface', explain: 'Adapter explanation', original: 'Original source fingerprint',
  manifest: 'Execution settings', changes: 'Added adapter files', unchanged: 'This project supplies its own interface; no adapter files were added.',
  check: 'I reviewed the execution settings and adapter code, and confirm this exact version.', approve: 'Confirm version',
  approveHint: 'Tick the self-check box above to enable the confirmation button.', fileSelected: 'Selected file',
  uploading: 'Uploading {n}%', retrying: 'Network unstable, retrying…',
  testPassed: 'Public scenario test passed', testResult: 'Download public test result', projectDownload: 'Download this project version', phase: 'Evaluation phase', evaluate: 'Evaluate this version',
  batches: 'Evaluations', local: 'Start local CSV session', localHelp: 'Run locally with the same step-by-step information. Upload the resulting decisions.csv after the session.',
  download: 'Download private result', uploadCsv: 'Upload matching CSV', average: 'Combined score',
  downloadAll: 'Download all results (ZIP)', downloadAllProgress: 'Downloading {done}/{total}…',
  downloadAllDone: 'All results downloaded.', downloadAllPartial: 'Downloaded — some cards failed; see errors.txt in the ZIP.',
  downloadAllFailed: 'Could not download any card’s result. Try again, or download them individually below.',
  api: 'Model APIs', apiHelp: 'The platform does not require model calls; awards require LLM-driven agent techniques in at least two stages. Team keys stay on the server. Set the model parameter to the call name below; OPENAI_BASE_URL and OPENAI_API_KEY are provided for each run. Each run and provider has separate limits.', callName: 'Model call name',
  shared: 'Organizer API', own: 'Team API', modelNames: 'Model names, separated by commas', endpoint: 'API endpoint', key: 'API key',
  apiName: 'API name', edit: 'Edit', limit: 'Daily token limit', saveKey: 'Save encrypted key', disable: 'Disable', enabled: 'Enabled', disabled: 'Disabled',
  evidence: 'Design award evidence', evidenceHelp: 'Describe the architecture and reproducible steps. This does not change performance scores.',
  codeUrl: 'Code or documentation URL (optional)', saveEvidence: 'Save evidence', notes: 'Architecture and reproduction notes',
  close: 'Close review', done: 'Saved.', prepared: 'Project queued for preparation. When it is ready, open the review in step 2 below.', confirmed: 'Version confirmed. Start it in step 3 below.',
  queued: 'Evaluation queued.', failed: 'This request could not be completed. Refresh and try again.', working: 'Working…',
  team: 'Join or create a team first.', phaseUnavailable: 'No evaluation phase is open.',
  final: 'Final version', finalIntro: 'After the online phase ends, the organizers evaluate your team’s final version 3 times on each of the hidden cards E–H (900 s per card); each card’s score is the mean of its 3 evaluations, which run at the same time (make sure your model service can handle concurrent calls). Only the mean over E–H decides the final ranking; the online board does not.',
  selfCheck: 'Evaluate 3 times and average', selfCheckNote: 'Self-check: like the final, this evaluates the same version 3 times in a row. The record shows the mean per card and overall and the range (lowest–highest) across the 3 evaluations, so you can see how stable your agent is. It uses 3 of today’s evaluations. Each of the 3 evaluations counts as an ordinary evaluation on the online board (which keeps your best single evaluation); the 3-evaluation mean is not shown on the board.',
  selfCheckNeed: 'Evaluate 3 times and average needs 3 of today’s evaluations.', selfCheckConfirm: 'Evaluate this version 3 times in a row? This uses 3 of today’s evaluations ({n} left today).',
  selfCheckQueued: 'Queued: the 3 evaluations run one after another.', selfCheckTitle: 'Evaluate 3 times and average (self-check)',
  selfCheckDone: '{done} of {total} evaluations scored', selfCheckCards: 'Mean per card (lowest–highest)', selfCheckOverall: 'Combined mean (lowest–highest)',
  selfCheckOff: 'Not used for the leaderboard.', selfCheckOne: 'Evaluation {n} of {total} in a 3-evaluation self-check',
  noModel: 'This evaluation without a model', noModelPill: 'No model',
  noModelHelp: 'For comparing your agent with and without an LLM: the program gets none of the variables tagged “model” under Keys and network (API keys, base URLs, model names) and OBSERVER_MODEL_DISABLED=1. Everything else, network access included, is unchanged. Applies to the next “Evaluate” or “Evaluate 3 times and average” only; it counts as an ordinary evaluation. The hidden final always uses your normal configuration.',
  noModelOn: 'The next evaluation runs without a model.',
  finalDefault: 'If you do not choose, the version of your team’s best evaluation is used.', finalDeadline: 'You can change the choice until',
  finalLocked: 'The choice is locked. This version will be evaluated on the hidden cards E–H.', finalChosen: 'Chosen by your team', finalBest: 'Default: best evaluation',
  finalNone: 'No final version yet. Confirm a version and evaluate it, or choose one below.', finalSet: 'Set as final version', finalClear: 'Clear choice',
  finalClearConfirm: 'Clear your choice? The version of your best evaluation will be used instead.', finalSaved: 'Final version saved.', finalCleared: 'Choice cleared; the default applies.',
  finalBadge: 'Final version', finalScore: 'score',
  finalRelay: 'If your program calls a large model, switch the model API to “Save encrypted on the server” before the competition ends; otherwise model calls will fail in the hidden final evaluation.',
  concurrent: 'Running now', concurrentOf: 'of', blocked: 'Unavailable',
  block_busy: 'Wait for the action in progress to finish.', block_phase_closed: 'This phase is not taking evaluations now.',
  block_no_quota: 'No evaluations left today; the count resets at the time shown above.',
  block_active_limit: 'Your team already has {n} evaluations running; start the next one when one finishes.',
  block_self_check_running: 'An “Evaluate 3 times and average” set is already running; only one set runs at a time.',
  block_self_check_quota: 'Evaluate 3 times and average needs 3 of today’s evaluations.',
  submitBlocked: 'Today’s uploads are used up; they reset at the time shown above.', approveNeedsTest: 'This version did not pass the public scenario test, so it cannot be confirmed. Open its logs, fix the project and upload again.',
  configured: 'Configured', otherVars: '{n} other variables', noVars: 'Nothing configured yet',
  latestFailed: 'Latest evaluation failed', latestCardFailed: 'Latest evaluation has a failed card', openFailLog: 'View the failure log', notCounted: 'not counted toward the daily limit', latestPill: 'Latest · failed',
  apiFinalNote: 'Nobody keeps a page open during the hidden final evaluation: teams whose program calls a model must switch to “Save encrypted on the server” before the online phase ends.',
}, {
  title: '智能体项目', intro: '提交完整项目，测试接口后，确认用于评测的具体版本。',
  localInfo: '本地运行信息', runner: '下载本地运行器', credential: '本次临时凭证', copy: '复制凭证',
  localCommand: '解压运行器后，替换项目路径并运行下面的命令，按提示粘贴凭证；凭证输入不会显示。',
  expires: '凭证到期时间', localSecret: '凭证只用于这次运行，请勿提交到仓库。',
  step1: '第1步 · 上传项目', step1Note: '上传不占评测次数。', prepPerDay: '每天可上传', prepLeft: '今天还剩',
  step2: '第2步 · 检查并确认版本', step2Note: '不占评测次数。准备完成后点“检查接口”，核对运行设置和适配代码，再确认版本。',
  step3: '第3步 · 开始评测', step3Note: '每点一次占当天 1 次；一次评测会把本赛程全部场景各跑一遍，分数是这些场景的平均分；排行榜取本队最高的一次；因平台原因失败的不计次数。',
  left: '今天还剩', perDay: '每天', dailyLimit: '每日评测上限', active: '本队最多可同时进行 {n} 个评测，请等其中一个结束后再开始。',
  noneLeft: '今天的评测次数已用完。', noApproved: '请先在第2步确认一个版本。', evaluated: '已评测', times: '次', confirmedAt: '确认于',
  evaluateAgain: '再评测一次', repeat: '这个版本已经评测过。再评测一次会再占用今天 1 次评测', repeatLeft: '今天还剩', proceed: '确定继续吗？',
  withdraw: '撤回', withdrawConfirm: '撤回这个版本？撤回后它会被隐藏，不能再确认或评测；已用的上传次数不退回。',
  withdrawn: '已撤回。', withdrawnPill: '已撤回', showWithdrawn: '显示已撤回的版本', hideWithdrawn: '隐藏已撤回的版本',
  duplicate: '几分钟前刚提交过相同的项目。确定再提交一次吗？这会占用今天的 1 次上传机会。',
  logs: '查看日志', refunded: '未计入次数', noBatches: '还没有评测记录。',
  prepareAgain: '重新准备', prepareAgainConfirm: '用这个仓库同一分支（未指定则为默认分支）和子目录的最新 commit 重新准备？这会占用今天的 1 次上传机会。',
  reuploadZip: '请修正后重新上传 ZIP（已上传的 ZIP 不会保留用于重试）。',
  closed: '当前比赛尚未开放项目评测。', csv: '原有 CSV 提交', newProject: '提交项目',
  name: '项目名称', repository: '公开 GitHub 仓库', zip: '私有 ZIP 上传',
  branch: '分支、标签或 commit（选填，留空用默认分支）', subdir: '项目所在子目录（选填，留空为仓库根目录）',
  repositoryHint: '也可以直接粘贴分支或子目录的链接，例如 https://github.com/owner/project/tree/dev/agent。提交时会记录当时的具体 commit。',
  privacy: '公开仓库 Fork 后仍然公开；ZIP 项目和详细结果只供本队与主办方查看。', file: '完整项目 ZIP · 最大 50 MB',
  submit: '上传并准备', projects: '我的项目', empty: '还没有项目。', refresh: '刷新', review: '检查接口', explain: '适配说明',
  original: '原始代码指纹', manifest: '运行设置', changes: '新增的适配文件', unchanged: '项目自带接口，没有新增适配文件。',
  check: '我已检查运行设置和适配代码，确认使用这个版本。', approve: '确认版本',
  approveHint: '先勾选上面的自查框，才能点确认版本。', fileSelected: '已选文件',
  uploading: '正在上传 {n}%', retrying: '网络不稳定，正在重试…', testPassed: '公开场景测试通过', testResult: '下载公开测试结果', projectDownload: '下载此版本项目',
  phase: '评测赛程', evaluate: '评测此版本', batches: '评测记录', local: '启动本地 CSV 会话',
  localHelp: '在本机运行，按步骤获得相同信息；运行结束后上传生成的 decisions.csv。', download: '下载私有结果',
  uploadCsv: '上传匹配的 CSV', average: '综合成绩',
  downloadAll: '下载全部结果（ZIP）', downloadAllProgress: '正在下载 {done}/{total}…',
  downloadAllDone: '全部结果已下载。', downloadAllPartial: '已下载——部分卡片失败，详见 ZIP 中的 errors.txt。',
  downloadAllFailed: '所有卡片的结果都下载失败，请重试，或在下方单独下载。',
  api: '模型 API', apiHelp: '平台不强制调用模型，但评奖要求至少两个环节采用大模型驱动的智能体技术。队伍密钥保存在服务器。model 参数使用下方调用名；每次运行会提供 OPENAI_BASE_URL 和 OPENAI_API_KEY。运行与接口均有独立额度。', callName: '模型调用名',
  shared: '主办方接口', own: '队伍接口', modelNames: '模型名称，用逗号分隔', endpoint: 'API 地址', key: 'API 密钥',
  apiName: '接口名称', edit: '修改', limit: '每天最多使用的 token 数', saveKey: '加密保存密钥', disable: '停用', enabled: '已启用', disabled: '已停用',
  evidence: '设计奖材料', evidenceHelp: '说明项目架构和复现步骤；这里不影响实际成绩。', codeUrl: '代码或文档链接（选填）',
  saveEvidence: '保存材料', notes: '架构和复现说明', close: '关闭检查', done: '已保存。', prepared: '项目已排队，等待准备。准备好后请到下方第2步点“检查接口”。',
  confirmed: '已确认版本。请到下方第3步开始评测。', queued: '已加入评测队列。', failed: '操作未完成，请刷新后重试。', working: '处理中…',
  team: '请先加入或创建队伍。', phaseUnavailable: '当前没有开放的评测赛程。',
  final: '最终版本', finalIntro: '线上赛结束后，主办方会在隐藏任务卡 E–H 上对每队的最终版本各评测 3 次（每张卡 900 秒），每张卡的成绩取 3 次评测的平均分；每张卡的 3 次评测同时进行，请确保你的模型服务能承受并发调用。最终排名只看 E–H 四张卡的平均分，线上榜不决定最终排名。',
  selfCheck: '评测 3 次取平均', selfCheckNote: '自检工具：与决赛相同，将同一版本连续评测 3 次，评测记录中显示各卡及综合的平均分，以及 3 次之间的最低–最高分，便于检查智能体是否稳定。占用今天 3 次评测。这 3 次评测各自按普通评测计入线上榜（线上榜取单次最高分），3 次的平均分不上榜。',
  selfCheckNeed: '「评测 3 次取平均」需要今天剩余至少 3 次评测。', selfCheckConfirm: '将对此版本连续评测 3 次，占用今天 3 次评测（今天还剩 {n} 次）。确定继续吗？',
  selfCheckQueued: '已加入评测队列，3 次评测将依次进行。', selfCheckTitle: '评测 3 次取平均（自检）',
  selfCheckDone: '已完成 {done}/{total} 次', selfCheckCards: '各卡平均分（最低–最高）', selfCheckOverall: '综合平均分（最低–最高）',
  selfCheckOff: '不计入排行榜。', selfCheckOne: '自检第 {n}/{total} 次',
  noModel: '本次不提供模型', noModelPill: '无模型',
  noModelHelp: '用于对比有无大模型时的表现：程序拿不到「密钥与网络」中标记为「模型相关」的变量（API 密钥、接口地址、模型名等），并会收到 OBSERVER_MODEL_DISABLED=1；其他设置（包括网络访问）不变。只对接下来的一次「评测」或「评测 3 次取平均」生效，照常占用评测次数、计入线上榜。隐藏卡决赛始终使用本队的正常配置。',
  noModelOn: '接下来的评测将不提供模型。',
  finalDefault: '如果不选择，默认使用本队最高分那次评测的版本。', finalDeadline: '可修改至',
  finalLocked: '选择已锁定，将用这个版本参加隐藏任务卡 E–H 的评测。', finalChosen: '本队已选择', finalBest: '默认：最高分评测',
  finalNone: '还没有最终版本。请先确认并评测一个版本，或在下方选择。', finalSet: '设为最终版本', finalClear: '取消选择',
  finalClearConfirm: '取消选择？将改用本队最高分评测的版本。', finalSaved: '已保存最终版本。', finalCleared: '已取消选择，恢复默认。',
  finalBadge: '最终版本', finalScore: '分数',
  finalRelay: '如果你的程序会调用大模型，请在比赛结束前把模型 API 改为『加密保存』，否则隐藏任务卡 E–H 评测时模型调用会失败。',
  concurrent: '同时进行', concurrentOf: '/', blocked: '暂不可用',
  block_busy: '请等正在进行的操作完成。', block_phase_closed: '当前赛程暂不接受评测。',
  block_no_quota: '今天的评测次数已用完，按上方的时间重置。',
  block_active_limit: '本队已有 {n} 个评测在进行，等其中一个结束后再开始。',
  block_self_check_running: '已有一组「评测 3 次取平均」在进行，同一时间只能进行一组。',
  block_self_check_quota: '「评测 3 次取平均」需要今天剩余至少 3 次评测。',
  submitBlocked: '今天的上传次数已用完，按上方的时间重置。', approveNeedsTest: '这个版本没有通过公开场景测试，不能确认。请查看日志，修正项目后重新上传。',
  configured: '已配置', otherVars: '另有 {n} 个变量', noVars: '尚未配置',
  latestFailed: '最近一次评测失败', latestCardFailed: '最近一次评测有卡片失败', openFailLog: '查看失败日志', notCounted: '未计入次数', latestPill: '最近一次 · 失败',
  apiFinalNote: '隐藏任务卡 E–H 评测时不会有人打开本页面：程序会调用大模型的队伍，请在线上赛结束前改为『加密保存』。',
}))
const activePhases = computed(() => (data.value?.phases ?? []).filter(p => entryPhaseIds(competition).includes(p.phase_id) && p.phases.is_active &&
  (!p.phases.ends_at || Date.parse(p.phases.ends_at) > Date.now())))
const projectsOpen = computed(() => activePhases.value.some(p => p.projects_enabled))
const openPhases = computed(() => activePhases.value.filter(p => !p.phases.starts_at || Date.parse(p.phases.starts_at) <= Date.now()))
const selectedPhase = computed(() => openPhases.value.find(p => p.phase_id === phaseId.value))
// 线上赛 / 练习赛 (competition mode with practice open): the page's switch picks the phase, and the records
// below follow it; otherwise every evaluation is listed as before. A chosen phase that has ended (online after
// its deadline) is read-only: no phase is selected, so nothing can start, while its records, logs, downloads
// and the locked final version stay visible.
const phaseSwitch = computed(() => offersPracticeSwitch(competition))
function followEntry() {
  const wanted = entryPhase.value
  if (!phaseSwitch.value || !wanted) return
  phaseId.value = openPhases.value.some(p => p.phase_id === wanted) ? wanted : ''
}
watch(entryPhase, followEntry)
const listedBatches = computed(() => (data.value?.batches ?? []).filter(b => !phaseSwitch.value || b.phase_id === entryPhase.value))
watch(selectedPhase, p => emit('phase', p ? { name_en: p.phases.name_en, name_zh: p.phases.name_zh } : null))
const quota = computed(() => data.value?.quota?.find(q => q.phase_id === phaseId.value) ?? null)
// Team-wide daily uploads (project preparations); the database derives the limit from the evaluation quota.
const prep = computed(() => preparationQuota(data.value?.quota))
// Shown even before the quota RPC answers: the phase setting is the same number the database enforces.
const dailyLimit = computed(() => quota.value?.daily_batches ?? selectedPhase.value?.daily_batches ?? null)
// Up to max_active_evaluations of the team's evaluations may run at once (a self-check set counts once).
const activeLimit = computed(() => selectedPhase.value?.max_active_evaluations ?? 4)
const activeBatch = computed(() => activeEvaluations(data.value?.batches) >= activeLimit.value)
const selfCheckActive = computed(() => (data.value?.batches ?? []).some(b => !!b.repeat_group && ['queued', 'running'].includes(b.status)))
const shownProjects = computed(() => visibleProjects(data.value?.projects, showWithdrawn.value))
const hiddenCount = computed(() => withdrawnCount(data.value?.projects))
const approvedVersions = computed(() => (data.value?.projects ?? []).flatMap(p => p.observer_revisions
  .filter(r => r.status === 'approved' && !r.archived_at)
  .map(r => ({ title: p.title, revision: r, evaluated: countedEvaluations(data.value?.batches, r.id, phaseId.value) }))))
// After the phase closes no phase is selected; the competition's own phase still owns the choice.
const finalVersion = computed(() => finalVersionFor(data.value?.final_versions, phaseId.value || (competition.betaPhaseId ?? competition.phaseId ?? '')))
// Every confirmed version can be chosen, also after the phase stopped taking evaluations.
const finalCandidates = computed(() => (data.value?.projects ?? []).flatMap(p => p.observer_revisions
  .filter(r => r.status === 'approved' && !r.archived_at).map(r => ({ title: p.title, revision: r }))))
const titles = computed(() => new Map((data.value?.projects ?? []).flatMap(p => p.observer_revisions.map(r => [r.id, p.title] as const))))
const phaseName = (id: string) => { const p = data.value?.phases.find(x => x.phase_id === id)?.phases; return p ? pick(p.name_en, p.name_zh) : '' }
// Team-key runs have no practical token cap (1,000,000,000 or more is shown as uncapped).
const TOKENS_UNCAPPED = 1_000_000_000
const relayFinalRisk = computed(() => !teamEgress.value && relayMissesHiddenFinal(modelMode.value, !!finalVersion.value || competition.mode === 'competition'))
const modelLimits = computed(() => {
  const p = activePhases.value[0]
  if (!p || !(p.model_call_limit > 0)) return null
  const params = { calls: p.model_call_limit.toLocaleString(), tokens: p.model_token_limit.toLocaleString(), concurrency: p.model_concurrency ?? 1 }
  return { key: p.model_token_limit >= TOKENS_UNCAPPED ? 'submit.model_api.limits' : 'submit.model_api.limits_tokens', params }
})
const statuses = computed(() => pick<Record<string, string>>({ queued:'Queued', preparing:'Preparing', reviewable:'Ready for review', approved:'Confirmed',
  failed:'Failed', starting:'Starting', ready:'Ready', running:'Running', awaiting_csv:'Waiting for CSV', scored:'Scored', cancelled:'Cancelled' },
  { queued:'排队中', preparing:'准备中', reviewable:'等待确认', approved:'已确认', failed:'失败', starting:'启动中', ready:'已就绪',
    running:'运行中', awaiting_csv:'等待 CSV', scored:'已评分', cancelled:'已取消' }))
function errorMessage(e: unknown) {
  if (e instanceof ZipWithoutCodeError) {
    const seen = e.files.length ? e.files.join(', ') : pick('nothing but folders', '只有空文件夹')
    return pick(`No code files were found in the ZIP (only ${seen}). Make sure you zipped the folder that contains your program, or start from an official example — the examples include observer.project.json.`,
      `压缩包里没有找到代码文件（只有 ${seen}）。请确认打包的是包含程序的文件夹，或参考官方示例，示例自带 observer.project.json。`)
  }
  const code = e instanceof Error ? e.message : ''
  const messages: Record<string, string> = {
    stale_approval: pick('The version changed. Reopen the review before confirming.', '版本已变化，请重新打开并检查。'),
    csv_does_not_match_session: pick('This CSV differs from the server-recorded decisions.', '这个 CSV 与服务器记录的决策不一致。'),
    batch_already_active: pick(`Your team can run up to ${activeLimit.value} evaluations at a time.`, `本队最多可同时进行 ${activeLimit.value} 个评测。`),
    repeat_already_active: pick('An “Evaluate 3 times and average” set is already running. Start another when it finishes.', '已有一组「评测 3 次取平均」正在进行，请等它结束后再开始。'),
    preparation_limit: pick('Your team already has three projects being prepared.', '本队已有三个项目正在准备，请等待完成。'),
    preparation_daily_limit: prep.value
      ? pick(`Your team has used today’s ${prep.value.daily} project uploads. ${formatDailyReset(prep.value.resets_at, 'en')}.`, `本队今天的 ${prep.value.daily} 次上传机会已用完，${formatDailyReset(prep.value.resets_at, 'zh')}。`)
      : pick('Your team has used today’s project uploads. The count resets at 00:00 UTC (08:00 Beijing time).', '本队今天的上传机会已用完，每天北京时间 8 点（UTC 0 点）重置。'),
    local_session_not_ready: pick('The local engine is not ready yet, or the run has ended. Refresh its status.', '本地会话尚未启动或已经结束，请刷新查看状态。'),
    daily_limit: pick('The daily evaluation limit has been reached.', '今天的评测次数已用完。'),
    repeat_daily_limit: pick('Evaluate 3 times and average needs 3 of today’s evaluations.', '「评测 3 次取平均」需要今天剩余至少 3 次评测。'),
    revision_already_evaluated: pick('This version has already been evaluated.', '这个版本已经评测过。'),
    revision_withdrawn: pick('This version was withdrawn.', '这个版本已撤回。'),
    revision_not_withdrawable: pick('Only versions that are not being prepared and were never evaluated can be withdrawn.', '只能撤回未在准备中、也从未评测过的版本。'),
    wrong_file_type: pick('Choose a file with the required extension.', '请选择要求的文件类型。'),
    file_too_large: pick('The file is empty or exceeds the size limit.', '文件为空或超过大小限制。'),
    invalid_repository_url: pick('Enter a public GitHub link: https://github.com/owner/repository, optionally with /tree/<branch>/<folder> or /commit/<sha>.', '请输入公开 GitHub 仓库链接：https://github.com/owner/repository，可带 /tree/分支/子目录 或 /commit/提交号。'),
    repository_not_found: pick('This GitHub repository was not found. Check the owner and name, and that it is public.', '找不到这个 GitHub 仓库，请检查用户名、仓库名，并确认仓库是公开的。'),
    source_ref_not_found: pick('This branch, tag or commit does not exist in the repository.', '仓库里没有这个分支、标签或 commit。'),
    source_subdir_not_found: pick('This folder does not exist at that branch or commit (folder names are case-sensitive).', '在这个分支或 commit 中找不到该子目录（区分大小写）。'),
    source_options_conflict: pick('The branch or folder in the link differs from the one entered below. Keep only one of them.', '链接里的分支或子目录与下面填写的不一致，请只保留一处。'),
    invalid_source_ref: pick('The branch, tag or commit name is not valid.', '分支、标签或 commit 名称格式不正确。'),
    invalid_source_subdir: pick('Enter the folder as a relative path such as agent or apps/agent.', '子目录请填写相对路径，例如 agent 或 apps/agent。'),
    source_options_unavailable: pick('Branch and folder choices are temporarily unavailable. Submit the plain repository link, or upload a ZIP.', '暂时不支持指定分支或子目录，请提交仓库主页链接，或上传 ZIP。'),
    invalid_source_archive: pick('This folder contains links or files that cannot be packaged. Upload the project as a ZIP instead.', '该目录包含符号链接或无法打包的文件，请改为上传 ZIP。'),
    private_source_requires_zip: pick('This repository is private. Make it public, or upload the project as a ZIP.', '这个仓库是私有的。请把它设为公开，或改为上传 ZIP。'),
    source_too_large: pick('The repository archive is larger than 100 MB. Upload a smaller ZIP of the project instead.', '仓库压缩包超过 100 MB，请改为上传精简后的项目 ZIP。'),
    source_snapshot_unavailable: pick('Could not save a copy of this repository version right now. Please try again in a minute.', '暂时无法保存该仓库版本的副本，请稍后再试。'),
    model_destination_not_enabled: t('submit.model_api.endpoint_refused'),
    invalid_team_model: t('submit.model_api.invalid'),
    invalid_team_variable: t('submit.team_env.invalid_variable'),
    team_variable_limit: t('submit.team_env.variable_limit'),
    team_variable_not_found: pick('This variable no longer exists. Refresh the page.', '这个变量已不存在，请刷新页面。'),
    no_model_not_available: pick('“Without a model” is only available for project evaluations in the online competition and practice, not in the hidden final.', '「本次不提供模型」只能用于线上赛和练习的项目评测，不能用于隐藏卡决赛。'),
    invalid_team_domains: t('submit.team_env.invalid_domain'),
    team_domain_not_public: t('submit.team_env.domain_not_public'),
    invalid_egress_route: pick('Choose direct, China route or overseas route.', '请选择直连、回国代理或海外代理。'),
    egress_route_unavailable: pick('Egress routes are not offered right now; evaluations connect directly.', '出网线路暂未开放，评测直接连接。'),
    final_version_locked: pick('The online phase has ended; the final version can no longer change.', '线上赛已结束，最终版本不能再修改。'),
    revision_not_approved: pick('Only a confirmed version can be chosen.', '只能选择已确认的版本。'),
    upload_limit: pick('Too many uploads are still pending for your team. Wait a few minutes for them to clear, then try again.', '本队有太多上传正在等待处理，请等几分钟后再试一次。'),
    upload_failed: pick('The file upload failed, possibly due to the network. Please try again.', '文件上传失败，可能是网络问题，请重试。'),
    upload_not_found: pick('The upload session expired or could not be found. Choose the file again and retry.', '上传会话已过期或找不到，请重新选择文件后再试一次。'),
    upload_not_finished: pick('The file has not finished uploading yet. Wait a moment and try again.', '文件还没有上传完成，请稍等再试一次。'),
    portal_unavailable: pick('Could not reach the server. Check your connection and try again.', '无法连接服务器，请检查网络后重试。'),
  }
  return code === 'cancelled' ? '' : messages[code] ?? words.value.failed
}
// Scenario names for the runs of each evaluation. Only listed scenarios are readable; others stay unnamed.
const scenarioNames = ref<Record<string, { slug: string; name: string }>>({})
async function loadScenarioNames() {
  const ids = [...new Set((data.value?.batches ?? []).flatMap(b => b.observer_runs.map(run => run.scenario_id)))]
    .filter(id => id && !(id in scenarioNames.value))
  if (!ids.length) return
  const { data: rows } = await supabase.from('scenarios').select('id,slug,name').in('id', ids)
  scenarioNames.value = { ...scenarioNames.value, ...Object.fromEntries((rows ?? []).map(row => [row.id, { slug: row.slug, name: row.name }])) }
}
async function reload() {
  data.value = await portal<PortalData>('list')
  void loadScenarioNames().catch(() => {})
  locked.value = new Set()
  modeChoice.value = modelMode.value
  protocolChoice.value = teamModelProtocol(data.value?.team_model)
  // Saved keys stay collapsed behind their summary; only things that need attention open the panel
  // (a relay key connected in this tab, or a missing relay key).
  if (!modelOpen.value && (personal.everConfigured.value || data.value?.team_environment?.relay_key_missing)) modelOpen.value = true
  await personal.refresh()
  // Bind evaluations to the entry phase (beta entry first), never to whatever
  // order the database happened to return.
  const preferred=entryPhase.value
  if (!openPhases.value.some(p => p.phase_id === phaseId.value)) phaseId.value = openPhases.value.find(p => p.phase_id===preferred)?.phase_id ?? openPhases.value[0]?.phase_id ?? ''
  followEntry()
}
async function action(work: () => Promise<void>, success = words.value.done, key = '') {
  if (busy.value || (key && locked.value.has(key))) return
  busy.value = true; pending.value = key; error.value = ''; notice.value = ''; retryStatus.value = ''
  try { await work() } catch (e) { error.value = errorMessage(e); busy.value = false; pending.value = ''; retryStatus.value = ''; return }
  // The request succeeded even if the refresh below fails; never invite a retry.
  if (key) locked.value.add(key)
  notice.value = success; retryStatus.value = ''
  try { await reload() } catch { /* the periodic refresh retries and unlocks */ } finally { busy.value = false; pending.value = '' }
}
async function zipCheck(file: File): Promise<string> {
  // Refused here before uploading (and again by the server): no preparation is used.
  let names: string[] | null = null
  try { names = zipEntryNames(await file.arrayBuffer()) } catch { names = null }
  if (!names) return ''
  const check = inspectSourceNames(names)
  if (!check.manifest && !check.code) throw new ZipWithoutCodeError(check.files)
  return check.manifest ? '' : pick(' No observer.project.json: your model will adapt the project automatically, which may fail.',
    ' 没有 observer.project.json：将使用你的模型自动适配，可能失败。')
}
async function submit() {
  const url = form.value.kind === 'repository' ? form.value.url : null
  if (recentDuplicate(data.value?.projects, form.value.title, url) && !window.confirm(words.value.duplicate)) return
  let warning = ''
  if (url === null && selectedFile.value) {
    try { warning = await zipCheck(selectedFile.value) } catch (e) { error.value = errorMessage(e); notice.value = ''; return }
  }
  const onRetry = () => { retryStatus.value = words.value.retrying }
  void action(async () => {
    if (url !== null) await portal('submit_repository', { title: form.value.title, url, branch: form.value.branch, subdir: form.value.subdir }, onRetry)
    else {
      if (!selectedFile.value) throw new Error('wrong_file_type')
      uploadPercent.value = 0
      let upload_id: string
      try { upload_id = await uploadProjectFile(selectedFile.value, 'source', p => { uploadPercent.value = p }, onRetry) }
      finally { uploadPercent.value = null }
      await portal('submit_zip', { title: form.value.title, upload_id }, onRetry)
    }
    form.value = { title: '', kind: form.value.kind, url: '', branch: '', subdir: '' }; selectedFile.value = null
    const file = document.querySelector<HTMLInputElement>('[data-testid="project-zip"]'); if (file) file.value = ''
  }, words.value.prepared + warning, 'submit').then(() => {
    // Point at step 2 after a successful upload; on failure the error banner stays in view instead.
    if (!error.value) document.querySelector('[data-testid="project-versions"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}
// Resubmits the same public repository through the ordinary upload action (a new revision).
function prepareAgain(title: string, r: ProjectRevision) {
  if (!window.confirm(words.value.prepareAgainConfirm)) return
  void action(async () => { await portal('submit_repository', { title, url: r.source_location, branch: r.source_ref ?? '', subdir: r.source_subdir ?? '' }) }, words.value.prepared, 'again:' + r.id)
}
function openReview(r: ProjectRevision) {
  review.value = r; confirmed.value = false; notes.value = r.observer_evidence?.notes ?? ''; codeUrl.value = r.observer_evidence?.code_url ?? ''
  void nextTick(() => reviewPanel.value?.focus())
}
function approve() { if (review.value && confirmed.value) { const id = review.value.id; void action(async () => {
  await portal('approve', { revision_id: id, digest: review.value!.approval_digest }); review.value = null
}, words.value.confirmed, 'approve:' + id).then(() => {
  if (!error.value) document.querySelector('[data-testid="project-evaluate"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}) } }
const repeatQuestion = () => sentences(words.value.repeat + (quota.value ? pick(` (${quota.value.remaining} ${words.value.repeatLeft}).`, `（${words.value.repeatLeft} ${quota.value.remaining} 次）。`) : pick('.', '。')), words.value.proceed)
function evaluate(revision_id: string) {
  const phase_id = phaseId.value
  // The server also refuses an unconfirmed repeat (the list may be incomplete); ask then.
  let repeat = countedEvaluations(data.value?.batches, revision_id, phase_id) > 0
  if (repeat && !window.confirm(repeatQuestion())) return
  void action(async () => {
    try { await portal('evaluate', { phase_id, revision_id, ...(repeat ? { confirm_repeat: true } : {}), ...noModelField() }) }
    catch (e) {
      if (repeat || !(e instanceof Error) || e.message !== 'revision_already_evaluated') throw e
      if (!window.confirm(repeatQuestion())) throw new Error('cancelled')
      repeat = true
      await portal('evaluate', { phase_id, revision_id, confirm_repeat: true, ...noModelField() })
    }
    noModel.value = false
  }, words.value.queued, 'evaluate:' + revision_id)
}
// The self-check: SELF_CHECK_RUNS evaluations of one version, run one after another (observer_create_repeat_batches).
function selfCheck(revision_id: string) {
  const phase_id = phaseId.value
  if (!window.confirm(words.value.selfCheckConfirm.replace('{n}', String(quota.value?.remaining ?? '?')))) return
  void action(async () => {
    const { error } = await supabase.rpc('observer_create_repeat_batches', { p_phase: phase_id, p_revision: revision_id, p_confirm_repeat: true,
      ...(noModel.value ? { p_no_model: true } : {}) })
    if (error) throw new Error(error.message)
    noModel.value = false
  }, words.value.selfCheckQueued, 'selfcheck:' + revision_id)
}
const repeats = computed(() => repeatSummaries(data.value?.batches))
/** 本次不提供模型: applies to the next evaluation (or self-check set) only, then switches itself off. */
const noModel = ref(false)
const noModelField = () => noModel.value ? { no_model: true } : {}
// What the collapsed keys panel shows: the configured model services, other variables, or the saved key.
const keysSummary = computed(() => {
  if (teamEgress.value) {
    const vars = data.value?.team_environment?.variables ?? []
    const services = configuredServices(vars)
    const used = new Set(services.flatMap(s => [s.names.key, s.names.baseUrl, s.names.model]))
    const parts = services.map(s => `${s.label}${s.model ? ' · ' + s.model : ''} (${s.prefix}_*)`)
    const rest = vars.filter(v => !used.has(v.name)).length
    if (rest) parts.push(words.value.otherVars.replace('{n}', String(rest)))
    return parts.join('；')
  }
  return savedModel.value ? `${savedModel.value.base_url} · ${savedModel.value.model}` : ''
})
const blockState = computed(() => ({ busy: busy.value, phaseEnabled: !!selectedPhase.value?.projects_enabled, quota: quota.value,
  activeAtLimit: activeBatch.value, selfCheckActive: selfCheckActive.value }))
const blockText = (b: EvaluateBlock) => b ? (words.value as Record<string, string>)['block_' + b]!.replace('{n}', String(activeLimit.value)) : ''
const evalBlocked = computed(() => evaluateBlock(blockState.value))
const selfCheckBlocked = computed(() => evaluateBlock(blockState.value, true))
const runningNow = computed(() => activeEvaluations(data.value?.batches))
// The newest evaluation when it failed: marked in the list and announced at the top with a link to its log.
const failure = computed(() => latestFailure(data.value?.batches))
function openFailure() {
  const f = failure.value
  if (!f) return
  if (f.run) openLogs.value = 'run:' + f.run.id
  void nextTick(() => document.getElementById('batch-' + f.batch.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}
// The position of an evaluation in its self-check (1 = the first one started).
// A platform-failed evaluation (refunded) is replaced automatically at the end of its set and gets no number.
function repeatIndex(b: { id: string; repeat_group?: string | null }) {
  const own = (data.value?.batches ?? []).filter(x => x.repeat_group === b.repeat_group && !(x.status === 'failed' && x.quota_refunded)).sort((x, y) => x.created_at.localeCompare(y.created_at) || x.id.localeCompare(y.id))
  return own.findIndex(x => x.id === b.id) + 1
}
// The summary goes above the newest evaluation of its self-check (the list is newest first).
const firstOfGroup = (b: { id: string; repeat_group?: string | null }) => !!b.repeat_group && (data.value?.batches ?? []).find(x => x.repeat_group === b.repeat_group)?.id === b.id
const fmt2 = (v: number) => v.toFixed(2)
function setFinal(revision_id: string | null) {
  const final = finalVersion.value
  if (!final || (revision_id === null && !window.confirm(words.value.finalClearConfirm))) return
  void action(async () => { await portal('set_final_version', { phase_id: final.phase_id, revision_id }) },
    revision_id ? words.value.finalSaved : words.value.finalCleared, 'final:' + (revision_id ?? 'clear'))
}
function withdraw(revision_id: string) {
  if (!window.confirm(words.value.withdrawConfirm)) return
  void action(async () => { await portal('withdraw', { revision_id }); if (review.value?.id === revision_id) review.value = null },
    words.value.withdrawn, 'withdraw:' + revision_id)
}
function toggleLogs(key: string) { openLogs.value = openLogs.value === key ? '' : key }
function chooseMode() {
  const mode = modeChoice.value, hadKey = !!savedModel.value
  if (mode === modelMode.value) return
  // Choosing the relay deletes a saved key on the server immediately.
  void action(async () => {
    try { await portal('set_team_model_mode', { mode, protocol: protocolChoice.value }) }
    catch (e) { modeChoice.value = modelMode.value; throw e }
    replacingKey.value = false
  }, mode === 'relay' ? sentences(t('submit.model_api.relay_selected'), ...(hadKey ? [t('submit.model_api.deleted_notice')] : []))
    : t('submit.model_api.stored_selected'))
}
// Relay has no other save gesture, so a protocol change persists as soon as it is already the chosen mode.
function chooseProtocol() {
  if (modelMode.value !== 'relay') return
  void action(async () => { await portal('set_team_model_mode', { mode: 'relay', protocol: protocolChoice.value }) })
}
function saveModel() { void action(async () => {
  try { await portal('save_team_model', { ...modelForm.value, protocol: protocolChoice.value }) } finally { modelForm.value.key = '' }
  replacingKey.value = false
}, t('submit.model_api.saved_notice')) }
function deleteModel() { void action(async () => { await portal('delete_team_model') }, t('submit.model_api.deleted_notice')) }
function download(run_id: string) { downloadFile('download_result', { run_id }, 'observer-result.zip') }
function downloadProject(revision_id: string) { downloadFile('download_project', { revision_id }, 'observer-project.zip') }
function downloadFile(command: string, fields: Record<string, unknown>, filename: string) { void action(async () => {
  const result = await portal<{ url: string }>(command, fields)
  const url = new URL(result.url)
  if (url.protocol !== 'https:' && url.hostname !== '127.0.0.1') throw new Error('invalid_download')
  const anchor = document.createElement('a'); anchor.href = url.href; anchor.rel = 'noreferrer'; anchor.download = filename; anchor.click()
}) }
function cardFolder(run: { id: string; scenario_id: string }): string {
  return cardFolderName(scenarioNames.value[run.scenario_id]?.slug ?? run.scenario_id, run.id)
}
function sortedRuns<T extends { scenario_id: string }>(runs: T[]): T[] {
  return [...runs].sort((a, b) => scenarioOrder(scenarioNames.value[a.scenario_id]?.slug ?? '') - scenarioOrder(scenarioNames.value[b.scenario_id]?.slug ?? ''))
}
async function downloadAllResults(batch: { id: string; created_at: string; phase_id: string; revision_id: string | null; model_disabled?: boolean
  repeat_group?: string | null; observer_runs: { id: string; scenario_id: string; result_path: string | null }[] }) {
  const runs = sortedRuns(batch.observer_runs.filter(r => r.result_path))
  if (!runs.length || zipProgress.value[batch.id]) return
  const outcome = (text: string, failed: boolean) => { zipOutcome.value = { ...zipOutcome.value, [batch.id]: { text, failed } } }
  const cleared = { ...zipOutcome.value }; delete cleared[batch.id]; zipOutcome.value = cleared
  zipProgress.value = { ...zipProgress.value, [batch.id]: { done: 0, total: runs.length } }
  try {
    const { unzipSync, zipSync, strToU8 } = await import('fflate')
    // Cards download in parallel; each one counts as soon as it arrives. The combined ZIP
    // still lists them in card order (α β γ δ / A B C D), one numbered folder per card.
    const cards = await Promise.all(runs.map(async (run, index) => {
      const folder = orderedCardFolder(index, runs.length, cardFolder(run))
      try {
        // One retry with a fresh signed URL: a single dropped connection (seen as
        // "Failed to fetch" / QUIC errors on flaky networks) should not lose a card.
        const fetchCard = async () => {
          const result = await portal<{ url: string }>('download_result', { run_id: run.id })
          const res = await fetch(result.url)
          if (!res.ok) throw new Error(`http_${res.status}`)
          return new Uint8Array(await res.arrayBuffer())
        }
        return { folder, entries: flattenResultEntries(unzipSync(await fetchCard().catch(fetchCard))) }
      } catch (e) {
        return { folder, error: e instanceof Error ? e.message : 'download_failed' }
      } finally {
        const prev = zipProgress.value[batch.id]
        zipProgress.value = { ...zipProgress.value, [batch.id]: { done: (prev?.done ?? 0) + 1, total: runs.length } }
      }
    }))
    const files: Record<string, Uint8Array> = {}
    const errors: string[] = []
    for (const card of cards) {
      if ('error' in card) { errors.push(`${card.folder}: ${card.error}`); continue }
      for (const [name, content] of Object.entries(card.entries)) files[`${card.folder}/${name}`] = content
    }
    if (!Object.keys(files).length) { outcome(words.value.downloadAllFailed, true); return }
    if (errors.length) files['errors.txt'] = strToU8(errors.join('\n') + '\n')
    files['evaluation.json'] = strToU8(JSON.stringify(evaluationMetadata(batch, (batch.revision_id && titles.value.get(batch.revision_id)) || null), null, 2) + '\n')
    const blob = new Blob([zipSync(files, { level: 6 })], { type: 'application/zip' })
    const date = new Date().toISOString().slice(0, 10)
    triggerDownload(blob, evaluationZipName(batch, date))
    outcome(errors.length ? words.value.downloadAllPartial : words.value.downloadAllDone, errors.length > 0)
  } catch {
    outcome(words.value.downloadAllFailed, true)
  } finally {
    const rest = { ...zipProgress.value }; delete rest[batch.id]; zipProgress.value = rest
  }
}
// ─── Simplified layout ("v2", behind the switch read by CompetitionWorkspacePage) ───────────────
// Same state and actions as the classic layout above; only the arrangement differs: things that need
// you, my progress + one next step, all quotas in one bar, then tabs (versions / evaluations / settings).
// It needs the team-variables model (team_environment.enabled); otherwise the classic layout is shown.
const v2 = computed(() => props.layout === 'v2' && teamEgress.value)
type V2Tab = 'progress' | 'history' | 'settings'
const V2_TAB_KEY = 'compete-v2-tab'
const v2Tab = ref<V2Tab>((() => {
  if (location.hash.startsWith('#batch-')) return 'history'
  const asked = tabFromQuery(new URLSearchParams(location.search).get('tab')) // deep link: /compete?tab=settings
  if (asked) return asked
  try { const saved = localStorage.getItem(V2_TAB_KEY); return saved === 'history' || saved === 'settings' ? saved : 'progress' } catch { return 'progress' }
})())
watch(v2Tab, tab => { try { localStorage.setItem(V2_TAB_KEY, tab) } catch { /* storage blocked */ } })
const uploadOpen = ref(false)
/** Evaluations opened in the 评测记录 tab (collapsed by default; a #batch- link opens its own). */
const openBatches = ref(new Set<string>(location.hash.startsWith('#batch-') ? [location.hash.slice(7)] : []))
function toggleBatch(id: string) { const next = new Set(openBatches.value); if (!next.delete(id)) next.add(id); openBatches.value = next }
// A successful upload closes the form; the new version appears in the table.
watch(notice, text => { if (text === words.value.prepared) uploadOpen.value = false })
const w2 = computed(() => pick({
  todo: 'Needs your attention', progress: 'My progress', autoRefresh: 'Refreshes every 15 s', refreshNow: 'Refresh now', nextStep: 'Next step',
  s1: 'Upload', s2: 'Review and confirm', s3: 'Evaluate', s4: 'Results',
  s1None: 'Nothing uploaded yet', s1Some: '{n} versions · latest {when}',
  s2Some: '{a} confirmed · {r} to review · {p} preparing', s3Some: 'Running {a}/{l} · {q} left today', s4None: 'No score yet', s4Best: 'Best {score} · {title}',
  qEval: 'Evaluations today', qRunning: 'Running at once', qPrep: 'Uploads today', qReset: 'Resets', left: 'left', limit: 'limit',
  qRunningHelp: 'Your team can run up to {n} evaluations at once; an “Evaluate 3 times and average” set counts as one, and only one set runs at a time.',
  qNote: 'Uploading and confirming do not use evaluations; evaluations that fail because of the platform are not counted.',
  tabProgress: 'Versions', tabHistory: 'Evaluations', tabSettings: 'Settings',
  versions: 'My versions', versionsHelp: 'Each upload is a version. Confirm a version before evaluating it.', upload: 'Upload a new version', collapse: 'Hide',
  colVersion: 'Version', colStatus: 'Status', colEvals: 'Evaluations', colBest: 'Best', colActions: 'Actions',
  uploaded: 'Uploaded', more: 'More', reviewConfirm: 'Review and confirm', evidenceItem: 'Design award evidence', lastFailed: 'last failed', lastCardFailed: 'a card failed',
  latest: 'Latest evaluation', allEvaluations: 'All evaluations', expandHint: 'Newest first; click a row for the cards, downloads and logs.',
  keys: 'Keys and network', keysNote: 'Evaluations and the hidden final use these variables; no page needs to stay open.',
  tokens: 'Personal API tokens (command line)', classic: 'Classic layout',
  nClosed: 'Project evaluation is not open for the current competition.', nFirst: 'Upload your first project to get started.',
  nFailed: 'The latest evaluation failed ({what}).', nFixUpload: 'Upload a fixed version',
  nReview: '“{title}” is ready. Review the settings and adapter code, then confirm it.', nPreparing: '“{title}” is being prepared (usually 1–3 minutes).',
  nRunning: '{n} evaluations running. Results appear in Evaluations.', nEvaluate: '“{title}” is confirmed and has not been evaluated yet.',
  nBest: 'Best score {score} ({title}). Improve and upload a new version, or check stability with “Evaluate 3 times and average”.',
  nUpload: 'Upload a new version.', viewLogs: 'View logs', viewEvaluations: 'View evaluations',
}, {
  todo: '待处理事项', progress: '我的进度', autoRefresh: '每 15 秒自动刷新', refreshNow: '立即刷新', nextStep: '下一步',
  s1: '上传', s2: '检查并确认', s3: '评测', s4: '结果',
  s1None: '还没有上传', s1Some: '{n} 个版本 · 最近 {when}',
  s2Some: '已确认 {a} · 待确认 {r} · 准备中 {p}', s3Some: '运行中 {a}/{l} · 今天还剩 {q} 次', s4None: '还没有成绩', s4Best: '最佳 {score} · {title}',
  qEval: '今天的评测', qRunning: '同时进行的评测', qPrep: '今天的上传', qReset: '重置时间', left: '剩余', limit: '上限',
  qRunningHelp: '本队最多同时进行 {n} 个评测；「评测 3 次取平均」算 1 个，且同一时间只能有 1 组。',
  qNote: '上传和确认都不占评测次数；因平台原因失败的评测不计次数。',
  tabProgress: '进度与版本', tabHistory: '评测记录', tabSettings: '设置',
  versions: '我的版本', versionsHelp: '每次上传是一个版本；确认后才能评测。', upload: '上传新版本', collapse: '收起',
  colVersion: '版本', colStatus: '状态', colEvals: '评测', colBest: '最佳分', colActions: '操作',
  uploaded: '上传于', more: '更多', reviewConfirm: '检查并确认', evidenceItem: '设计奖材料', lastFailed: '最近失败', lastCardFailed: '有卡片失败',
  latest: '最近一次评测', allEvaluations: '全部评测记录', expandHint: '最新在上；点一行展开各卡分数、下载与日志。',
  keys: '密钥与网络', keysNote: '评测和隐藏决赛都使用这里的变量，无需开着页面。',
  tokens: '个人 API 令牌（命令行）', classic: '旧版布局',
  nClosed: '当前比赛尚未开放项目评测。', nFirst: '上传你的第一个项目，开始参赛。',
  nFailed: '最近一次评测失败（{what}）。', nFixUpload: '上传修正版',
  nReview: '「{title}」已准备好：核对运行设置和适配代码后确认版本。', nPreparing: '「{title}」正在准备（通常 1–3 分钟）。',
  nRunning: '{n} 个评测进行中，结果会出现在「评测记录」。', nEvaluate: '「{title}」已确认，还没有评测。',
  nBest: '最佳 {score}（{title}）。改进后上传新版本，或用「评测 3 次取平均」检查稳定性。',
  nUpload: '上传新版本。', viewLogs: '查看日志', viewEvaluations: '查看评测记录',
}))
const fill = (text: string, values: Record<string, string | number>) => text.replace(/\{(\w+)\}/g, (_, k: string) => String(values[k] ?? ''))
type VersionRow = { title: string; r: ProjectRevision; evaluations: number; best: number | null; lastFailed: boolean; lastCardFailed: boolean }
/** One row per version, newest first, with its counted evaluations and best combined score. */
const versionRows = computed<VersionRow[]>(() => shownProjects.value.flatMap(p => p.observer_revisions.map(r => {
  const own = (data.value?.batches ?? []).filter(b => b.revision_id === r.id).sort((a, b) => b.created_at.localeCompare(a.created_at))
  const scores = own.map(b => b.score).filter((s): s is number => s != null)
  const last = own[0]
  return { title: p.title, r, evaluations: countedEvaluations(data.value?.batches, r.id, phaseId.value),
    best: scores.length ? Math.max(...scores) : null, lastFailed: last?.status === 'failed',
    lastCardFailed: !!last && last.status !== 'failed' && last.observer_runs.some(run => run.status === 'failed') }
})).sort((a, b) => b.r.created_at.localeCompare(a.r.created_at)))
const live = computed(() => versionRows.value.filter(v => !v.r.archived_at))
const reviewable = computed(() => live.value.filter(v => v.r.status === 'reviewable'))
const preparing = computed(() => live.value.filter(v => ['queued', 'preparing'].includes(v.r.status)))
const best = computed(() => {
  const scored = (data.value?.batches ?? []).filter(b => b.score != null)
  const top = scored.reduce<(typeof scored)[number] | null>((m, b) => !m || b.score! > m.score! ? b : m, null)
  return top ? { score: top.score!, title: (top.revision_id && titles.value.get(top.revision_id)) || '' } : null
})
const newestBatch = computed(() => [...listedBatches.value].sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null)
const currentStep = computed(() => runningNow.value ? 3 : reviewable.value.length ? 2 : !live.value.length ? 1 : !approvedVersions.value.length ? 2 : !best.value ? 3 : 4)
const steps = computed(() => {
  const w = w2.value, newest = live.value[0]
  return [
    { n: 1, title: w.s1, done: live.value.length > 0, text: newest ? fill(w.s1Some, { n: live.value.length, when: when(newest.r.created_at) }) : w.s1None },
    { n: 2, title: w.s2, done: approvedVersions.value.length > 0 && !reviewable.value.length, text: fill(w.s2Some, { a: approvedVersions.value.length, r: reviewable.value.length, p: preparing.value.length }) },
    { n: 3, title: w.s3, done: (data.value?.batches ?? []).some(b => b.score != null) && !runningNow.value,
      text: fill(w.s3Some, { a: runningNow.value, l: activeLimit.value, q: quota.value?.remaining ?? '—' }) },
    { n: 4, title: w.s4, done: !!best.value, text: best.value ? fill(w.s4Best, { score: best.value.score.toFixed(2), title: best.value.title }) : w.s4None },
  ]
})
type NextStep = { kind: string; text: string; label?: string; run?: () => void; label2?: string; run2?: () => void }
function showUpload() {
  v2Tab.value = 'progress'; uploadOpen.value = true
  void nextTick(() => document.querySelector('[data-testid="project-upload-form"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}
function openFailure2() { v2Tab.value = 'progress'; openFailure() }
function openReview2(r: ProjectRevision) {
  openReview(r)
  void nextTick(() => document.querySelector('[data-testid="project-review"]')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
}
const nextStep = computed<NextStep>(() => {
  const w = w2.value
  if (!projectsOpen.value) return { kind: 'closed', text: w.nClosed }
  const f = failure.value
  if (f && f.batch.id === newestBatch.value?.id) {
    const what = [when(f.batch.created_at), f.batch.revision_id ? titles.value.get(f.batch.revision_id) : ''].filter(Boolean).join(' · ')
    return { kind: 'failed', text: fill(w.nFailed, { what }), label: words.value.openFailLog, run: openFailure2, label2: w.nFixUpload, run2: showUpload }
  }
  const ready = reviewable.value[0]
  if (ready) return { kind: 'review', text: fill(w.nReview, { title: ready.title }), label: w.reviewConfirm, run: () => openReview2(ready.r) }
  if (runningNow.value) return { kind: 'running', text: fill(w.nRunning, { n: runningNow.value }), label: w.viewEvaluations, run: () => { v2Tab.value = 'history' } }
  const prep1 = preparing.value[0]
  if (prep1) return { kind: 'preparing', text: fill(w.nPreparing, { title: prep1.title }), label: w.viewLogs, run: () => { v2Tab.value = 'progress'; openLogs.value = 'rev:' + prep1.r.id } }
  if (!live.value.length) return { kind: 'first', text: w.nFirst, label: w.upload, run: showUpload }
  const fresh = live.value.find(v => v.r.status === 'approved' && !v.evaluations && !v.best)
  if (fresh && !evalBlocked.value) return { kind: 'evaluate', text: fill(w.nEvaluate, { title: fresh.title }), label: words.value.evaluate, run: () => evaluate(fresh.r.id) }
  if (best.value) return { kind: 'best', text: fill(w.nBest, { score: best.value.score.toFixed(2), title: best.value.title }), label: w.upload, run: showUpload }
  return { kind: 'upload', text: w.nUpload, label: w.upload, run: showUpload }
})
/** Closes the ⋯ menu a choice was made in. */
function closeMenu(event: Event) { (event.target as HTMLElement).closest('details')?.removeAttribute('open') }
function saveEvidence() {
  const r = review.value
  if (r) void action(async () => { await portal('evidence', { revision_id: r.id, notes: notes.value, code_url: codeUrl.value }) })
}
onMounted(async () => {
  await refreshMeCached()
  try { if (team.value) await reload() } catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false }
  // Links from the records page point at one evaluation; show it once it is loaded.
  if (location.hash.startsWith('#batch-')) {
    targetBatch.value = location.hash.slice(7)
    void nextTick(() => document.getElementById(location.hash.slice(1))?.scrollIntoView({ block: 'center' }))
  }
  timer = setInterval(() => { if (!busy.value && team.value && !document.hidden) void reload().catch(() => {}) }, 15000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <section data-testid="project-workflow">
    <p v-if="!v2" class="text2 mb-5">{{ words.intro }}</p>
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <p v-else-if="!team" class="panel">{{ words.team }} <router-link to="/team">{{ t('nav.team') }}</router-link></p>
    <template v-else-if="v2">
      <div class="cw-alerts" data-testid="compete-todo">
        <p v-if="error" class="errors" role="alert" data-testid="project-error">{{ error }}</p>
        <p v-if="notice" role="status" class="cw-notice">{{ notice }}</p>
        <p v-if="data?.team_environment?.relay_key_missing" class="errors" role="alert" data-testid="team-env-relay-banner">{{ t('submit.team_env.relay_banner') }}</p>
      </div>

      <section class="cw-progress" data-testid="progress-panel" aria-labelledby="cw-progress-title">
        <div class="cw-progress-head">
          <h2 id="cw-progress-title">{{ w2.progress }}</h2>
          <span v-if="selectedPhase" class="pill info">{{ pick(selectedPhase.phases.name_en, selectedPhase.phases.name_zh) }}</span>
          <span class="meta cw-grow-left">{{ w2.autoRefresh }} · <button type="button" class="cw-link" :disabled="busy" data-testid="compete-refresh" @click="action(reload)">{{ w2.refreshNow }}</button></span>
        </div>
        <ol class="cw-steps">
          <li v-for="s in steps" :key="s.n" class="cw-step" :class="{ done: s.done, current: s.n === currentStep }" :aria-current="s.n === currentStep ? 'step' : undefined" :data-testid="'progress-step-' + s.n">
            <span class="cw-step-n">{{ s.n }}<template v-if="s.done"> ✓</template></span>
            <strong class="cw-step-t">{{ s.title }}</strong>
            <span class="cw-step-s">{{ s.text }}</span>
          </li>
        </ol>
        <div class="cw-next" data-testid="next-step" :data-kind="nextStep.kind">
          <span class="cw-next-label">{{ w2.nextStep }}</span>
          <span class="cw-next-text">{{ nextStep.text }}<span v-if="nextStep.kind === 'failed' && failure?.batch.quota_refunded" class="pill info ml-2">{{ words.notCounted }}</span></span>
          <span class="cw-next-actions">
            <button v-if="nextStep.label" type="button" class="btn primary sm" :disabled="busy && nextStep.kind === 'evaluate'" data-testid="next-step-action" @click="nextStep.run?.()">{{ nextStep.label }}</button>
            <button v-if="nextStep.label2" type="button" class="btn sm" @click="nextStep.run2?.()">{{ nextStep.label2 }}</button>
          </span>
        </div>
      </section>

      <div class="cw-quota" data-testid="quota-bar">
        <div class="cw-q" data-testid="quota-evaluations"><span class="cw-q-k">{{ w2.qEval }}</span>
          <span class="cw-q-v">{{ quota?.remaining ?? '—' }} <small>/ {{ dailyLimit ?? '—' }} {{ w2.left }}</small></span>
          <span class="cw-bar"><i :style="{ width: (quota && dailyLimit ? Math.round(100 * quota.remaining / dailyLimit) : 0) + '%' }"></i></span></div>
        <div class="cw-q" data-testid="evaluation-active-count"><span class="cw-q-k" :title="fill(w2.qRunningHelp, { n: activeLimit })">{{ w2.qRunning }} ⓘ</span>
          <span class="cw-q-v">{{ runningNow }} <small>/ {{ activeLimit }} {{ w2.limit }}</small></span>
          <span class="cw-bar"><i :style="{ width: Math.min(100, Math.round(100 * runningNow / activeLimit)) + '%' }"></i></span></div>
        <div class="cw-q" data-testid="preparation-quota"><span class="cw-q-k">{{ w2.qPrep }}</span>
          <span class="cw-q-v"><span data-testid="preparation-remaining">{{ prep?.remaining ?? '—' }}</span> <small>/ {{ prep?.daily ?? '—' }} {{ w2.left }}</small></span>
          <span class="cw-bar"><i :style="{ width: (prep && prep.daily ? Math.round(100 * prep.remaining / prep.daily) : 0) + '%' }"></i></span></div>
        <div class="cw-q" data-testid="evaluation-reset"><span class="cw-q-k">{{ w2.qReset }}</span>
          <span class="cw-q-reset">{{ formatDailyReset(quota?.resets_at ?? prep?.resets_at, locale) }}</span></div>
      </div>
      <p class="help mt-2">{{ w2.qNote }} <span class="sr-only">{{ fill(w2.qRunningHelp, { n: activeLimit }) }}</span></p>

      <div class="cw-tabs" role="tablist" data-testid="compete-tabs">
        <button v-for="tab in (['progress', 'history', 'settings'] as const)" :key="tab" type="button" role="tab" class="cw-tab" :class="{ on: v2Tab === tab }"
          :aria-selected="v2Tab === tab" :data-testid="'compete-tab-' + tab" @click="v2Tab = tab">
          {{ tab === 'progress' ? w2.tabProgress : tab === 'history' ? w2.tabHistory : w2.tabSettings }}<span v-if="tab === 'history' && listedBatches.length" class="meta ml-2">{{ listedBatches.length }}</span>
        </button>
      </div>

      <!-- 进度与版本 -->
      <div v-if="v2Tab === 'progress'" role="tabpanel">
        <section class="panel mt-4" data-testid="project-versions">
          <div class="cw-row-head">
            <h2 id="review">{{ w2.versions }}</h2><span class="help">{{ w2.versionsHelp }}</span>
            <button v-if="projectsOpen && !(uploadOpen || !live.length)" type="button" class="btn primary sm cw-grow-left" data-testid="project-upload-open" @click="showUpload">＋ {{ w2.upload }}</button>
          </div>
          <form v-if="projectsOpen && (uploadOpen || !live.length)" class="cw-upload" data-testid="project-upload-form" @submit.prevent="submit">
            <div class="cw-row-head"><h3 id="prepare">{{ w2.upload }}</h3>
              <span class="help"><template v-if="prep">{{ pick(`${prep.remaining} ${words.prepLeft}`, `${words.prepLeft} ${prep.remaining} 次`) }} · </template>{{ words.step1Note }}</span>
              <button v-if="live.length" type="button" class="cw-link cw-grow-left" @click="uploadOpen = false">{{ w2.collapse }}</button></div>
            <label class="field"><span>{{ words.name }}</span><input v-model="form.title" type="text" name="project-title" required maxlength="100" :placeholder="pick('e.g. my-agent v1','例如：my-agent v1')" autocomplete="off" data-testid="project-title"></label>
            <label class="check"><input v-model="form.kind" type="radio" value="repository">{{ words.repository }}</label>
            <label class="check"><input v-model="form.kind" type="radio" value="zip">{{ words.zip }}</label>
            <label v-if="form.kind === 'repository'" class="field"><span>{{ words.repository }}</span><input v-model="form.url" type="url" required placeholder="https://github.com/owner/project" data-testid="project-url"></label>
            <template v-if="form.kind === 'repository'">
              <p class="help mb-3">{{ words.repositoryHint }}</p>
              <label class="field"><span>{{ words.branch }}</span><input v-model="form.branch" type="text" maxlength="200" placeholder="main" autocomplete="off" data-testid="project-branch"></label>
              <label class="field"><span>{{ words.subdir }}</span><input v-model="form.subdir" type="text" maxlength="300" placeholder="agent" autocomplete="off" data-testid="project-subdir"></label>
            </template>
            <label v-else class="field border border-dashed border-border-subtle p-5"><span>{{ words.file }}</span><input type="file" accept=".zip,application/zip" required data-testid="project-zip" @change="selectedFile = ($event.target as HTMLInputElement).files?.[0] ?? null"></label>
            <p v-if="form.kind === 'zip' && selectedFile" class="help mt-2" data-testid="project-zip-selected">{{ words.fileSelected }}: {{ selectedFile.name }} · {{ bytes(selectedFile.size) }}</p>
            <p v-if="uploadPercent != null" class="help mt-2" role="status" data-testid="project-upload-progress">
              {{ words.uploading.replace('{n}', String(uploadPercent)) }}
              <progress class="upload-progress" :value="uploadPercent" max="100"></progress>
            </p>
            <p v-if="retryStatus" class="help mt-2" role="status" data-testid="project-retry-status">{{ retryStatus }}</p>
            <p class="help mb-4">{{ words.privacy }}</p>
            <button class="btn primary" :disabled="busy || locked.has('submit') || (prep != null && prep.remaining <= 0)" data-testid="project-submit">{{ busy ? words.working : words.submit }}</button>
            <p v-if="prep != null && prep.remaining <= 0" class="help mt-2" data-testid="project-submit-blocked">{{ words.submitBlocked }}</p>
          </form>
          <p v-if="!projectsOpen" class="help mt-3">{{ words.closed }}</p>

          <label v-if="approvedVersions.length" class="check no-model-option mt-3" :title="words.noModelHelp" data-testid="evaluation-no-model">
            <input v-model="noModel" type="checkbox" name="observer-no-model" :disabled="busy" data-testid="evaluation-no-model-input">{{ words.noModel }}</label>
          <p v-if="approvedVersions.length" class="help no-model-help" :class="{ on: noModel }" data-testid="evaluation-no-model-help">{{ noModel ? words.noModelOn + ' ' : '' }}{{ words.noModelHelp }}</p>
          <div v-if="versionRows.length" class="cw-table" role="table">
            <div class="cw-tr cw-th" role="row"><span role="columnheader">{{ w2.colVersion }}</span><span role="columnheader">{{ w2.colStatus }}</span><span role="columnheader">{{ w2.colEvals }}</span><span role="columnheader">{{ w2.colBest }}</span><span role="columnheader" class="cw-right">{{ w2.colActions }}</span></div>
            <template v-for="v in versionRows" :key="v.r.id">
              <div class="cw-tr" role="row" :data-revision-id="v.r.id" data-testid="version-row">
                <span class="cw-td-name" role="cell"><strong>{{ v.title }}</strong>
                  <span v-if="finalRole(finalVersion, v.r.id)" class="pill ok ml-2" data-testid="final-version-badge">{{ words.finalBadge }}</span>
                  <span class="meta cw-block">{{ w2.uploaded }} {{ when(v.r.created_at) }}<template v-if="v.r.approved_at"> · {{ words.confirmedAt }} {{ when(v.r.approved_at) }}</template></span>
                  <span v-if="v.r.source_ref || v.r.source_subdir" class="meta cw-block break-all" data-testid="revision-source">{{ [v.r.source_ref, v.r.source_subdir].filter(Boolean).join(' · ') }}</span></span>
                <span role="cell"><span class="pill" :class="{ ok: v.r.status === 'approved' && !v.r.archived_at, failed: v.r.status === 'failed' && !v.r.archived_at, running: ['queued','preparing'].includes(v.r.status), info: v.r.status === 'reviewable' }">{{ v.r.archived_at ? words.withdrawnPill : statuses[v.r.status] ?? v.r.status }}</span></span>
                <span role="cell" class="cw-evals"><template v-if="v.evaluations">{{ pick(`${v.evaluations}×`, `${v.evaluations} 次`) }}</template><template v-else>—</template>
                  <span v-if="v.lastFailed" class="pill failed ml-2">{{ w2.lastFailed }}</span><span v-else-if="v.lastCardFailed" class="pill failed ml-2">{{ w2.lastCardFailed }}</span></span>
                <span role="cell" class="cw-score" :class="{ best: best && v.best === best.score }">{{ v.best != null ? v.best.toFixed(2) : '—' }}</span>
                <span role="cell" class="cw-actions">
                  <template v-if="!v.r.archived_at && v.r.status === 'approved'">
                    <button type="button" class="btn sm" :class="{ primary: !v.evaluations }" :disabled="busy || locked.has('evaluate:'+v.r.id) || !selectedPhase?.projects_enabled || activeBatch || (quota != null && quota.remaining <= 0)" :title="blockText(evalBlocked)" data-testid="project-evaluate-button" :aria-busy="pending === 'evaluate:'+v.r.id" @click="evaluate(v.r.id)">{{ pending === 'evaluate:'+v.r.id ? words.working : v.evaluations ? words.evaluateAgain : words.evaluate }}</button>
                    <button type="button" class="btn sm" :disabled="busy || locked.has('selfcheck:'+v.r.id) || !selectedPhase?.projects_enabled || activeBatch || selfCheckActive || !canSelfCheck(quota)" :title="selfCheckBlocked ? blockText(selfCheckBlocked) : words.selfCheckNote" data-testid="project-self-check-button" :aria-busy="pending === 'selfcheck:'+v.r.id" @click="selfCheck(v.r.id)">{{ pending === 'selfcheck:'+v.r.id ? words.working : words.selfCheck }}</button>
                  </template>
                  <button v-else-if="!v.r.archived_at && v.r.status === 'reviewable'" type="button" class="btn primary sm" data-testid="project-review-open" @click="openReview2(v.r)">{{ w2.reviewConfirm }}</button>
                  <button v-if="canPrepareAgain(v.r) && projectsOpen" type="button" class="btn sm" :disabled="busy || locked.has('again:'+v.r.id)" data-testid="project-prepare-again" @click="prepareAgain(v.title, v.r)">{{ words.prepareAgain }}</button>
                  <button type="button" class="btn sm" :aria-expanded="openLogs === 'rev:'+v.r.id" data-testid="revision-logs" @click="toggleLogs('rev:'+v.r.id)">{{ words.logs }}</button>
                  <details class="cw-menu" data-testid="version-menu">
                    <summary class="btn sm" :aria-label="w2.more">⋯</summary>
                    <div class="cw-menu-list" @click="closeMenu">
                      <button v-if="!v.r.archived_at && (['reviewable','approved'].includes(v.r.status) || (v.r.status === 'failed' && v.r.manifest))" type="button" @click="openReview2(v.r)">{{ words.review }}</button>
                      <button type="button" :disabled="busy" @click="downloadProject(v.r.id)">{{ words.projectDownload }}</button>
                      <button v-if="v.r.public_test?.passed && v.r.public_test.run_id" type="button" :disabled="busy" @click="download(v.r.public_test.run_id!)">{{ words.testResult }}</button>
                      <button v-if="finalVersion && v.r.status === 'approved' && !v.r.archived_at && canChooseFinal(finalVersion, v.r.id)" type="button" :disabled="busy || locked.has('final:'+v.r.id)" data-testid="final-version-set" @click="setFinal(v.r.id)">{{ words.finalSet }}</button>
                      <button v-if="!v.r.archived_at && ['reviewable','approved'].includes(v.r.status)" type="button" @click="openReview2(v.r)">{{ w2.evidenceItem }}</button>
                      <button v-if="canWithdraw(v.r, data?.batches) && !(data?.final_versions ?? []).some(f => f.chosen_revision_id === v.r.id)" type="button" class="cw-danger" :disabled="busy || locked.has('withdraw:'+v.r.id)" data-testid="project-withdraw" @click="withdraw(v.r.id)">{{ words.withdraw }}</button>
                    </div>
                  </details>
                </span>
              </div>
              <p v-if="v.r.error && !v.r.archived_at" class="errors cw-sub" role="status" data-testid="revision-error">{{ revisionErrorText(v.r.error, locale) }}<template v-if="v.r.status === 'failed' && v.r.source_kind === 'zip'"> {{ words.reuploadZip }}</template></p>
              <div v-if="openLogs === 'rev:'+v.r.id" class="cw-sub"><RunLogs :target="{ revision_id: v.r.id, test_run_id: v.r.public_test?.run_id }" :label="v.title + ' · ' + when(v.r.created_at)" :file-stem="'public-test-' + v.title" :statuses="statuses" @close="openLogs = ''" /></div>
              <section v-if="review?.id === v.r.id" tabindex="-1" class="cw-sub cw-review" data-testid="project-review" aria-live="polite">
                <div class="cw-row-head"><h3>{{ words.review }}</h3><p v-if="review.public_test.passed" class="pill ok">{{ words.testPassed }}</p>
                  <button type="button" class="cw-link cw-grow-left" @click="review = null">{{ words.close }}</button></div>
                <p v-if="!review.public_test.passed && review.error" class="errors mt-3">{{ revisionErrorText(review.error, locale) }}</p>
                <p class="mt-3">{{ words.explain }}: {{ review.explanation }}</p>
                <p class="help break-all">{{ words.original }}: {{ review.source_digest }}</p>
                <h4 class="mt-4">{{ words.manifest }}</h4><pre>{{ JSON.stringify(manifestForDisplay(review.manifest), null, 2) }}</pre>
                <h4 class="mt-4">{{ words.changes }}</h4><p v-if="!Object.keys(review.adapter_files).length" class="help">{{ words.unchanged }}</p>
                <div v-for="(code, path) in review.adapter_files" :key="path"><h4 class="break-all">{{ path }}</h4><pre>{{ code }}</pre></div>
                <template v-if="review.status === 'reviewable' && !review.archived_at"><label class="check mt-4"><input v-model="confirmed" type="checkbox" data-testid="project-confirm">{{ words.check }}</label>
                  <p v-if="!confirmed" class="help mt-2" data-testid="project-approve-hint">{{ words.approveHint }}</p>
                  <button class="btn primary mt-3" :disabled="busy || !confirmed || !review.public_test.passed || locked.has('approve:'+review.id)" data-testid="project-approve" @click="approve">{{ words.approve }}</button>
                  <p v-if="!review.public_test.passed" class="help mt-2" data-testid="project-approve-blocked">{{ words.approveNeedsTest }}</p></template>
                <form class="mt-5" data-testid="project-evidence" @submit.prevent="saveEvidence">
                  <h4>{{ words.evidence }}</h4><p class="help">{{ words.evidenceHelp }}</p>
                  <label class="field"><span>{{ words.notes }}</span><textarea v-model="notes" maxlength="8000" rows="5"></textarea></label>
                  <label class="field"><span>{{ words.codeUrl }}</span><input v-model="codeUrl" type="url" maxlength="1000"></label>
                  <button class="btn sm" :disabled="busy">{{ words.saveEvidence }}</button>
                </form>
              </section>
            </template>
          </div>
          <p v-if="approvedVersions.length && (evalBlocked || selfCheckBlocked) && evalBlocked !== 'busy'" class="help mt-3" role="status" data-testid="evaluation-disabled-reason">
            {{ words.blocked }}{{ pick(': ', '：') }}{{ blockText(evalBlocked ?? selfCheckBlocked) }}<template v-if="!evalBlocked && selfCheckBlocked"> ({{ words.selfCheck }})</template></p>
          <p v-if="approvedVersions.length" class="help mt-2">{{ words.selfCheckNote }}</p>
          <button v-if="hiddenCount" type="button" class="log-link mt-4" @click="showWithdrawn = !showWithdrawn">{{ showWithdrawn ? words.hideWithdrawn : words.showWithdrawn + ' (' + hiddenCount + ')' }}</button>
        </section>

        <section v-if="newestBatch" class="panel mt-4" data-testid="latest-evaluation">
          <div class="cw-row-head"><h2>{{ w2.latest }}</h2>
            <button type="button" class="cw-link cw-grow-left" @click="v2Tab = 'history'">{{ w2.allEvaluations }} →</button></div>
          <template v-for="b in [newestBatch]" :key="b.id">
            <article :id="'batch-'+b.id" class="cw-batch" :class="{ 'latest-failed': failure?.batch.id === b.id }">
              <p class="cw-batch-line">{{ when(b.created_at) }}<template v-if="b.revision_id && titles.get(b.revision_id)"> · {{ titles.get(b.revision_id) }}</template>
                <span class="pill ml-2" :class="b.status">{{ statuses[b.status] ?? b.status }}</span>
                <span v-if="b.quota_refunded" class="pill info ml-2" data-testid="batch-refunded">{{ words.refunded }}</span>
                <span v-if="isNoModel(b)" class="pill no-model ml-2" :title="words.noModelHelp" data-testid="batch-no-model">{{ words.noModelPill }}</span>
                <span v-if="b.score != null" class="cw-score ml-2">{{ words.average }}: {{ b.score.toFixed(2) }}</span></p>
              <div class="cw-cards">
                <div v-for="run in sortedRuns(b.observer_runs)" :key="run.id" class="cw-card" :class="{ failed: run.status === 'failed' }">
                  <span class="meta">{{ scenarioNames[run.scenario_id] ? scenarioLabel(scenarioNames[run.scenario_id]!.slug, scenarioNames[run.scenario_id]!.name, locale) : '—' }}</span>
                  <strong class="cw-card-score">{{ run.score != null ? run.score.toFixed(2) : statuses[run.status] ?? run.status }}</strong>
                  <span class="cw-card-links">
                    <button v-if="run.result_path" type="button" class="cw-link" :disabled="busy" @click="download(run.id)">{{ words.download }}</button>
                    <button type="button" class="cw-link" :aria-expanded="openLogs === 'run:'+run.id" data-testid="run-logs-button" @click="toggleLogs('run:'+run.id)">{{ words.logs }}</button></span>
                </div>
              </div>
              <template v-for="run in b.observer_runs" :key="'log'+run.id">
                <RunLogs v-if="openLogs === 'run:'+run.id" :target="{ run_id: run.id }" :label="scenarioNames[run.scenario_id] ? scenarioLabel(scenarioNames[run.scenario_id]!.slug, scenarioNames[run.scenario_id]!.name, locale) : when(b.created_at)" :file-stem="cardFolder(run)" :statuses="statuses" @close="openLogs = ''" />
              </template>
            </article>
          </template>
        </section>
      </div>

      <!-- 评测记录 -->
      <section v-else-if="v2Tab === 'history'" role="tabpanel" class="panel mt-4" data-testid="evaluation-history">
        <div class="cw-row-head"><h2 id="results">{{ words.batches }}</h2><span class="help">{{ w2.expandHint }}</span></div>
        <p v-if="!listedBatches.length" class="text3 mt-3">{{ words.noBatches }}</p>
        <template v-for="b in listedBatches" :key="b.id">
          <article v-if="firstOfGroup(b) && repeats.get(b.repeat_group!)" class="cw-batch repeat-summary" data-testid="self-check-summary">
            <p><strong>{{ words.selfCheckTitle }}</strong><template v-if="b.revision_id && titles.get(b.revision_id)"> · {{ titles.get(b.revision_id) }}</template>
              · {{ words.selfCheckDone.replace('{done}', String(repeats.get(b.repeat_group!)!.scored)).replace('{total}', String(repeats.get(b.repeat_group!)!.runs)) }}
              <span class="pill info ml-2">{{ words.selfCheckOff }}</span>
              <span v-if="isNoModel(b)" class="pill no-model ml-2" data-testid="self-check-no-model">{{ words.noModelPill }}</span></p>
            <p v-if="repeats.get(b.repeat_group!)!.overall" class="mt-2" data-testid="self-check-overall">{{ words.selfCheckOverall }}: <strong>{{ fmt2(repeats.get(b.repeat_group!)!.overall!.mean) }}</strong>
              <span class="meta">({{ fmt2(repeats.get(b.repeat_group!)!.overall!.min) }}–{{ fmt2(repeats.get(b.repeat_group!)!.overall!.max) }})</span></p>
            <template v-if="repeats.get(b.repeat_group!)!.cards.length"><p class="meta mt-2">{{ words.selfCheckCards }}</p>
            <div class="repeat-cards"><span v-for="c in repeats.get(b.repeat_group!)!.cards" :key="c.scenario_id" class="repeat-card" data-testid="self-check-card">
              <span class="m text-sm">{{ scenarioNames[c.scenario_id] ? scenarioLabel(scenarioNames[c.scenario_id]!.slug, scenarioNames[c.scenario_id]!.name, locale) : '—' }}</span>
              <strong>{{ fmt2(c.mean) }}</strong> <span class="meta">({{ fmt2(c.min) }}–{{ fmt2(c.max) }})</span></span></div></template>
          </article>
          <article :id="'batch-'+b.id" class="cw-batch" :class="{ target: b.id === targetBatch, 'repeat-member': !!b.repeat_group, 'latest-failed': failure?.batch.id === b.id }">
            <button type="button" class="cw-batch-toggle" :aria-expanded="openBatches.has(b.id)" data-testid="batch-toggle" @click="toggleBatch(b.id)">
              <span class="cw-caret">{{ openBatches.has(b.id) ? '▾' : '▸' }}</span>
              <span class="meta">{{ when(b.created_at) }}</span>
              <span class="cw-batch-title">{{ (b.revision_id && titles.get(b.revision_id)) || '—' }}<template v-if="phaseName(b.phase_id)"><span class="meta"> · {{ phaseName(b.phase_id) }}</span></template></span>
              <span class="cw-batch-pills"><span class="pill" :class="b.status">{{ statuses[b.status] ?? b.status }}</span>
                <span v-if="failure?.batch.id === b.id" class="pill failed" data-testid="batch-latest-failed">{{ words.latestPill }}</span>
                <span v-if="b.quota_refunded" class="pill info" data-testid="batch-refunded">{{ words.refunded }}</span>
                <span v-if="isNoModel(b)" class="pill no-model" :title="words.noModelHelp" data-testid="batch-no-model">{{ words.noModelPill }}</span>
                <span v-if="b.repeat_group && repeatIndex(b) > 0" class="pill" data-testid="batch-self-check">{{ words.selfCheckOne.replace('{n}', String(repeatIndex(b))).replace('{total}', String(b.repeat_runs ?? SELF_CHECK_RUNS)) }}</span></span>
              <span class="cw-score" :class="{ best: best && b.score === best.score }">{{ b.score != null ? b.score.toFixed(2) : '—' }}</span>
            </button>
            <div v-if="openBatches.has(b.id)" class="cw-batch-body">
              <p v-if="b.observer_runs.filter(r => r.result_path).length > 1" class="flex flex-wrap items-center gap-3">
                <button type="button" class="btn sm" :disabled="!!zipProgress[b.id]" data-testid="download-all-results" @click="downloadAllResults(b)">{{ zipProgress[b.id] ? words.downloadAllProgress.replace('{done}', String(zipProgress[b.id]!.done)).replace('{total}', String(zipProgress[b.id]!.total)) : words.downloadAll }}</button>
                <span v-if="zipOutcome[b.id]" :class="zipOutcome[b.id]!.failed ? 'errors' : ''" role="status" data-testid="download-all-outcome">{{ zipOutcome[b.id]!.text }}</span>
              </p>
              <div v-for="run in sortedRuns(b.observer_runs)" :key="run.id" class="flex flex-wrap gap-3 mt-3 items-center">
                <span v-if="scenarioNames[run.scenario_id]" class="m text-sm" :title="scenarioNames[run.scenario_id]!.slug" data-testid="run-scenario">{{ scenarioLabel(scenarioNames[run.scenario_id]!.slug, scenarioNames[run.scenario_id]!.name, locale) }}</span>
                <span class="pill" :class="run.status">{{ statuses[run.status] ?? run.status }}</span>
                <span v-if="run.score != null">{{ run.score_summary?.calibration ? t('leaderboard.calibrated_score') + ': ' : '' }}{{ run.score.toFixed(2) }}</span>
                <span v-if="run.score_summary?.raw_score" class="meta">{{ t('leaderboard.raw_score') }}: {{ run.score_summary.raw_score.total.toFixed(2) }}</span>
                <button v-if="run.result_path" class="btn sm" :disabled="busy" @click="download(run.id)">{{ words.download }}</button>
                <button type="button" class="btn sm" :aria-expanded="openLogs === 'run:'+run.id" data-testid="run-logs-button" @click="toggleLogs('run:'+run.id)">{{ words.logs }}</button>
                <RunLogs v-if="openLogs === 'run:'+run.id" :target="{ run_id: run.id }" :label="scenarioNames[run.scenario_id] ? scenarioLabel(scenarioNames[run.scenario_id]!.slug, scenarioNames[run.scenario_id]!.name, locale) : when(b.created_at)" :file-stem="cardFolder(run)" :statuses="statuses" @close="openLogs = ''" />
              </div>
            </div>
          </article>
        </template>
      </section>

      <!-- 设置 -->
      <div v-else role="tabpanel" class="cw-settings">
        <section class="panel mt-4" data-testid="model-api-settings">
          <div class="cw-row-head"><h2 id="model-api">{{ w2.keys }}</h2>
            <span v-if="keysSummary" class="pill ok" data-testid="model-api-configured">{{ words.configured }}</span>
            <span class="help">{{ w2.keysNote }}</span></div>
          <TeamEnvironment :environment="data?.team_environment" :busy="busy" @act="(work, success) => action(work, success)" />
        </section>
        <section v-if="finalVersion" class="panel mt-4" data-testid="final-version">
          <h2 id="final">{{ words.final }}</h2>
          <p class="help">{{ words.finalIntro }}</p>
          <p class="help">{{ words.finalDefault }}<template v-if="finalVersion.deadline && !finalVersion.locked"> {{ words.finalDeadline }} {{ when(finalVersion.deadline) }}.</template></p>
          <p v-if="finalVersion.locked" class="mt-3" role="status" data-testid="final-version-locked">{{ words.finalLocked }}</p>
          <p v-if="!finalVersion.revision_id" class="text3 mt-3">{{ words.finalNone }}</p>
          <p v-else class="mt-3 flex flex-wrap items-center gap-3" data-testid="final-version-current">
            <strong>{{ titles.get(finalVersion.revision_id) ?? finalVersion.revision_id }}</strong>
            <span class="pill ok">{{ finalVersion.source === 'chosen' ? words.finalChosen : words.finalBest }}</span>
            <span v-if="finalVersion.chosen_at && finalVersion.source === 'chosen'" class="meta">{{ when(finalVersion.chosen_at) }}</span>
            <span v-else-if="finalVersion.best_score != null" class="meta">{{ words.finalScore }} {{ finalVersion.best_score.toFixed(2) }}</span>
            <button v-if="canClearFinal(finalVersion)" type="button" class="btn sm" :disabled="busy || locked.has('final:clear')" data-testid="final-version-clear" @click="setFinal(null)">{{ words.finalClear }}</button>
          </p>
          <div v-for="v in finalCandidates" :key="v.revision.id" class="flex flex-wrap items-center gap-3 mt-3" :data-final-revision-id="v.revision.id">
            <span>{{ v.title }}</span>
            <span v-if="v.revision.approved_at" class="meta">{{ words.confirmedAt }} {{ when(v.revision.approved_at) }}</span>
            <span v-if="finalRole(finalVersion, v.revision.id)" class="pill ok">{{ words.finalBadge }}</span>
            <button v-if="canChooseFinal(finalVersion, v.revision.id)" type="button" class="btn sm" :disabled="busy || locked.has('final:'+v.revision.id)" data-testid="final-version-set" @click="setFinal(v.revision.id)">{{ words.finalSet }}</button>
          </div>
        </section>
        <KimiPlanPanel class="mt-4" />
        <section class="panel mt-4"><ApiTokensPanel /></section>
      </div>
      <p class="help mt-6">{{ pick('Bring your own model API; organizer credits are not provided. Awards require LLM-driven agent techniques in at least two stages (see Rules). Never include a permanent key in your repository or ZIP.','请自备模型 API，平台不提供额度。评奖要求至少两个环节采用大模型驱动的智能体技术（见规则）。不要把永久密钥放进仓库或 ZIP。') }}
        · <a href="?ui=v1" data-testid="compete-classic-link">{{ w2.classic }}</a></p>
    </template>
    <template v-else>
      <p v-if="error" class="errors" role="alert" data-testid="project-error">{{ error }}</p>
      <p v-if="notice" role="status" class="mb-4">{{ notice }}</p>
      <p v-if="failure" class="latest-failure mb-5" role="status" data-testid="latest-failure">
        <strong>{{ failure.batch.status === 'failed' ? words.latestFailed : words.latestCardFailed }}</strong>
        <span>{{ when(failure.batch.created_at) }}<template v-if="failure.batch.revision_id && titles.get(failure.batch.revision_id)"> · {{ titles.get(failure.batch.revision_id) }}</template></span>
        <span v-if="failure.batch.quota_refunded" class="pill info">{{ words.notCounted }}</span>
        <button type="button" class="btn primary sm" data-testid="latest-failure-logs" @click="openFailure">{{ words.openFailLog }}</button>
      </p>
      <p v-if="!projectsOpen" class="panel">{{ words.closed }}</p>
      <details class="panel mb-6 model-api" data-testid="model-api-settings" :open="modelOpen" @toggle="modelOpen = ($event.target as HTMLDetailsElement).open">
        <summary class="model-api-summary" data-testid="model-api-toggle">
          <span id="model-api" class="model-api-title" role="heading" aria-level="2">{{ teamEgress ? t('submit.team_env.title') : t('submit.model_api.title') }}</span>
          <span v-if="!modelOpen && keysSummary" class="model-api-configured" data-testid="model-api-configured"><span class="pill ok">{{ words.configured }}</span> {{ keysSummary }}</span>
          <span v-else class="help model-api-hint" data-testid="model-api-hint">{{ teamEgress ? (data?.team_environment?.open ? t('submit.team_env.collapsed_hint_open') : t('submit.team_env.collapsed_hint')) : t('submit.model_api.collapsed_hint') }}</span>
        </summary>
        <div v-if="teamEgress" class="model-api-body">
          <TeamEnvironment :environment="data?.team_environment" :busy="busy" @act="(work, success) => action(work, success)" />
        </div>
        <div v-else class="model-api-body">
        <p class="help mt-3">{{ t('submit.model_api.intro') }}</p>
        <p class="help" data-testid="model-mode-tradeoff">{{ t('submit.model_api.tradeoff') }}</p>
        <p v-if="relayFinalRisk" class="errors" role="note" data-testid="model-mode-final-note">{{ words.apiFinalNote }}</p>
        <fieldset class="mt-4" :disabled="busy">
          <legend class="sr-only">{{ t('submit.model_api.choice') }}</legend>
          <label class="check"><input v-model="modeChoice" type="radio" name="model-key-mode" value="stored" aria-describedby="model-mode-stored-help" data-testid="model-mode-stored" @change="chooseMode">{{ t('submit.model_api.stored') }}</label>
          <p id="model-mode-stored-help" class="help mb-3">{{ t('submit.model_api.stored_help') }}</p>
          <label class="check"><input v-model="modeChoice" type="radio" name="model-key-mode" value="relay" aria-describedby="model-mode-relay-help" data-testid="model-mode-relay" @change="chooseMode">{{ t('submit.model_api.relay') }}</label>
          <p id="model-mode-relay-help" class="help">{{ savedModel ? sentences(t('submit.model_api.relay_help'), t('submit.model_api.relay_deletes')) : t('submit.model_api.relay_help') }}</p>
        </fieldset>
        <p class="help mt-4">{{ t('submit.model_api.usage') }}</p>
        <!-- The form follows the choice at once; a refused change resets the choice (chooseMode). -->
        <template v-if="modeChoice === 'stored'">
          <p v-if="modelLimits" class="help">{{ tf(modelLimits.key, modelLimits.params) }}</p>
          <div v-if="savedModel && !replacingKey" class="mt-4" data-testid="team-model-saved">
            <h3>{{ t('submit.model_api.saved_title') }}</h3>
            <p class="help break-all">{{ savedModel.base_url }} · {{ savedModel.model }}</p>
            <p class="help" data-testid="team-model-hint">{{ savedModel.key_hint ? tf('submit.model_api.key_ending', { hint: savedModel.key_hint }) : t('submit.model_api.key_hidden') }} · {{ tf('submit.model_api.saved_at', { time: when(savedModel.saved_at) }) }}</p>
            <div class="flex flex-wrap gap-3 mt-3">
              <button type="button" class="btn sm" :disabled="busy" @click="replacingKey = true">{{ t('submit.model_api.replace') }}</button>
              <button type="button" class="btn sm" :disabled="busy" @click="deleteModel">{{ t('submit.model_api.delete') }}</button>
            </div>
          </div>
          <form v-else class="mt-4" data-testid="team-model-form" autocomplete="off" @submit.prevent="saveModel">
            <p v-if="!savedModel" class="help">{{ t('submit.model_api.none') }}</p>
            <label class="field"><span>{{ t('submit.model_api.protocol') }}</span>
              <select v-model="protocolChoice" name="observer-model-protocol" data-testid="team-model-protocol">
                <option value="openai">{{ t('submit.model_api.protocol_openai') }}</option>
                <option value="anthropic">{{ t('submit.model_api.protocol_anthropic') }}</option>
              </select>
            </label>
            <p class="help">{{ t('submit.model_api.protocol_hint') }}</p>
            <label class="field"><span>{{ t('submit.model_api.endpoint') }}</span><input v-model="modelForm.base_url" type="url" name="observer-model-endpoint" required pattern="https://.+" maxlength="1000" list="model-base-suggestions" placeholder="https://api.moonshot.cn/v1" autocomplete="off" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" spellcheck="false" aria-describedby="team-model-endpoint-help" data-testid="team-model-endpoint"></label>
            <p id="team-model-endpoint-help" class="help">{{ t('submit.model_api.endpoint_hint') }}</p>
            <label class="field"><span>{{ t('submit.model_api.model') }}</span><input v-model="modelForm.model" type="text" name="observer-model-name" maxlength="256" required autocomplete="off" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" spellcheck="false" data-testid="team-model-name"></label>
            <p class="help">{{ t('submit.model_api.model_hint') }}</p>
            <label class="field"><span>{{ t('submit.model_api.key') }}</span><input v-model="modelForm.key" type="password" name="observer-model-secret" autocomplete="new-password" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" maxlength="8192" required data-testid="team-model-key"></label>
            <div class="flex flex-wrap gap-3">
              <button class="btn sm" :disabled="busy">{{ t('submit.model_api.save') }}</button>
              <button v-if="savedModel" type="button" class="btn sm" :disabled="busy" @click="replacingKey = false; modelForm.key = ''">{{ t('submit.model_api.cancel') }}</button>
            </div>
          </form>
        </template>
        <form v-else class="mt-4" data-testid="personal-model-settings" autocomplete="off" @submit.prevent="action(personal.connect)">
          <p v-if="relayRunning && !personal.connected.value && personal.everConfigured.value" class="errors" role="alert">{{ t('submit.model_api.relay_running') }}</p>
          <p class="help">{{ t('submit.model_api.keep_open') }}</p>
          <label class="field"><span>{{ t('submit.model_api.protocol') }}</span>
            <select v-model="protocolChoice" name="observer-relay-protocol" data-testid="personal-model-protocol" @change="chooseProtocol">
              <option value="openai">{{ t('submit.model_api.protocol_openai') }}</option>
              <option value="anthropic">{{ t('submit.model_api.protocol_anthropic') }}</option>
            </select>
          </label>
          <p class="help">{{ t('submit.model_api.protocol_hint') }}</p>
          <label class="field"><span>{{ t('submit.model_api.endpoint') }}</span><input v-model="personal.endpoint.value" type="url" name="observer-relay-endpoint" :disabled="personal.connected.value" required pattern="https://.+" maxlength="1000" list="model-base-suggestions" placeholder="https://api.moonshot.cn/v1" autocomplete="off" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" spellcheck="false" aria-describedby="personal-model-endpoint-help" data-testid="personal-model-endpoint"></label>
          <p id="personal-model-endpoint-help" class="help">{{ t('submit.model_api.endpoint_hint') }}</p>
          <label class="field"><span>{{ t('submit.model_api.model') }}</span><input v-model="personal.model.value" type="text" name="observer-relay-model" :disabled="personal.connected.value" maxlength="256" required autocomplete="off" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" spellcheck="false" data-testid="personal-model-name"></label>
          <p class="help">{{ t('submit.model_api.model_hint') }}</p>
          <label class="field"><span>{{ t('submit.model_api.key') }}</span><input v-model="personal.key.value" type="password" name="observer-relay-secret" autocomplete="new-password" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" :disabled="personal.connected.value" maxlength="8192" required data-testid="personal-api-key"></label>
          <button v-if="!personal.connected.value" class="btn sm" :disabled="busy">{{ t('submit.model_api.connect') }}</button>
          <button v-else type="button" class="btn sm" @click="personal.clear">{{ t('submit.model_api.disconnect') }}</button>
          <p v-if="personal.connected.value" class="help mt-3" role="status">{{ personal.status.value==='failed'?t('submit.model_api.call_failed'):personal.status.value==='working'?t('submit.model_api.working'):t('submit.model_api.connected') }}</p>
        </form>
        <datalist id="model-base-suggestions"><option v-for="base in personalBases" :key="base" :value="base"></option></datalist>
        </div>
      </details>
      <p class="mb-5"><button type="button" class="btn sm" :disabled="busy" @click="action(reload)">{{ words.refresh }}</button></p>
      <form v-if="projectsOpen" class="panel mb-6" @submit.prevent="submit">
        <h2 id="prepare">{{ words.step1 }}</h2>
        <p class="help mb-4" data-testid="preparation-quota"><template v-if="prep"><strong>{{ pick(`${words.prepPerDay}: ${prep.daily}`, `${words.prepPerDay} ${prep.daily} 次`) }}</strong>
          · <strong data-testid="preparation-remaining">{{ pick(`${prep.remaining} ${words.prepLeft}`, `${words.prepLeft} ${prep.remaining} 次`) }}</strong>
          ({{ formatDailyReset(prep.resets_at, locale) }}) · </template>{{ words.step1Note }}</p>
        <label class="field"><span>{{ words.name }}</span><input v-model="form.title" type="text" name="project-title" required maxlength="100" :placeholder="pick('e.g. my-agent v1','例如：my-agent v1')" autocomplete="off" data-testid="project-title"></label>
        <label class="check"><input v-model="form.kind" type="radio" value="repository">{{ words.repository }}</label>
        <label class="check"><input v-model="form.kind" type="radio" value="zip">{{ words.zip }}</label>
        <label v-if="form.kind === 'repository'" class="field"><span>{{ words.repository }}</span><input v-model="form.url" type="url" required placeholder="https://github.com/owner/project" data-testid="project-url"></label>
        <template v-if="form.kind === 'repository'">
          <p class="help mb-3">{{ words.repositoryHint }}</p>
          <label class="field"><span>{{ words.branch }}</span><input v-model="form.branch" type="text" maxlength="200" placeholder="main" autocomplete="off" data-testid="project-branch"></label>
          <label class="field"><span>{{ words.subdir }}</span><input v-model="form.subdir" type="text" maxlength="300" placeholder="agent" autocomplete="off" data-testid="project-subdir"></label>
        </template>
        <label v-else class="field border border-dashed border-border-subtle p-5"><span>{{ words.file }}</span><input type="file" accept=".zip,application/zip" required data-testid="project-zip" @change="selectedFile = ($event.target as HTMLInputElement).files?.[0] ?? null"></label>
        <p v-if="form.kind === 'zip' && selectedFile" class="help mt-2" data-testid="project-zip-selected">{{ words.fileSelected }}: {{ selectedFile.name }} · {{ bytes(selectedFile.size) }}</p>
        <p v-if="uploadPercent != null" class="help mt-2" role="status" data-testid="project-upload-progress">
          {{ words.uploading.replace('{n}', String(uploadPercent)) }}
          <progress class="upload-progress" :value="uploadPercent" max="100"></progress>
        </p>
        <p v-if="retryStatus" class="help mt-2" role="status" data-testid="project-retry-status">{{ retryStatus }}</p>
        <p class="help mb-4">{{ words.privacy }}</p>
        <button class="btn primary" :disabled="busy || locked.has('submit') || (prep != null && prep.remaining <= 0)" data-testid="project-submit">{{ busy ? words.working : words.submit }}</button>
        <p v-if="prep != null && prep.remaining <= 0" class="help mt-2" data-testid="project-submit-blocked">{{ words.submitBlocked }}</p>
      </form>
      <section class="panel mb-6" data-testid="project-versions">
        <h2 id="review">{{ words.step2 }}</h2><p class="help">{{ words.step2Note }}</p>
        <p v-if="!shownProjects.length" class="text3 mt-3">{{ words.empty }}</p>
        <article v-for="p in shownProjects" :key="p.id" class="project-row">
          <h3>{{ p.title }}</h3>
          <div v-for="r in p.observer_revisions" :key="r.id" class="flex flex-wrap items-center gap-3 mt-3" :data-revision-id="r.id">
            <span class="pill">{{ r.archived_at ? words.withdrawnPill : statuses[r.status] ?? r.status }}</span>
            <span v-if="finalRole(finalVersion, r.id)" class="pill ok" data-testid="final-version-badge">{{ words.finalBadge }}</span>
            <span class="meta">{{ when(r.created_at) }}</span>
            <span v-if="r.source_ref || r.source_subdir" class="meta break-all" data-testid="revision-source">{{ [r.source_ref, r.source_subdir].filter(Boolean).join(' · ') }}</span>
            <span v-if="r.error && !r.archived_at" class="errors" role="status" data-testid="revision-error">{{ revisionErrorText(r.error, locale) }}<template v-if="r.status === 'failed' && r.source_kind === 'zip'"> {{ words.reuploadZip }}</template></span>
            <button v-if="canPrepareAgain(r) && projectsOpen" type="button" class="btn sm" :disabled="busy || locked.has('again:'+r.id)" data-testid="project-prepare-again" @click="prepareAgain(p.title, r)">{{ words.prepareAgain }}</button>
            <button v-if="!r.archived_at && (['reviewable','approved'].includes(r.status) || (r.status === 'failed' && r.manifest))" type="button" class="btn sm" :class="{ primary: r.status === 'reviewable' }" @click="openReview(r)">{{ words.review }}</button>
            <button v-if="canWithdraw(r, data?.batches) && !(data?.final_versions ?? []).some(f => f.chosen_revision_id === r.id)" type="button" class="btn sm" :disabled="busy || locked.has('withdraw:'+r.id)" data-testid="project-withdraw" :aria-busy="pending === 'withdraw:'+r.id" @click="withdraw(r.id)">{{ pending === 'withdraw:'+r.id ? words.working : words.withdraw }}</button>
            <button type="button" class="btn sm" :aria-expanded="openLogs === 'rev:'+r.id" data-testid="revision-logs" @click="toggleLogs('rev:'+r.id)">{{ words.logs }}</button>
            <RunLogs v-if="openLogs === 'rev:'+r.id" :target="{ revision_id: r.id, test_run_id: r.public_test?.run_id }" :label="p.title + ' · ' + when(r.created_at)" :file-stem="'public-test-' + p.title" :statuses="statuses" @close="openLogs = ''" />
          </div>
        </article>
        <button v-if="hiddenCount" type="button" class="log-link mt-4" @click="showWithdrawn = !showWithdrawn">{{ showWithdrawn ? words.hideWithdrawn : words.showWithdrawn + ' (' + hiddenCount + ')' }}</button>
      </section>
      <section v-if="review" ref="reviewPanel" tabindex="-1" class="panel mb-6" data-testid="project-review" aria-live="polite">
        <h2>{{ words.review }}</h2><p v-if="review.public_test.passed" class="pill ok mt-3">{{ words.testPassed }}</p>
        <p v-else-if="review.error" class="errors mt-3">{{ revisionErrorText(review.error, locale) }}</p>
        <div class="flex flex-wrap gap-3 mt-3">
          <button class="btn sm" :disabled="busy" @click="downloadProject(review.id)">{{ words.projectDownload }}</button>
          <button v-if="review.public_test.passed && review.public_test.run_id" class="btn sm" :disabled="busy" @click="download(review.public_test.run_id!)">{{ words.testResult }}</button>
        </div>
        <p class="mt-4">{{ words.explain }}: {{ review.explanation }}</p>
        <p class="help break-all">{{ words.original }}: {{ review.source_digest }}</p>
        <h3 class="mt-5">{{ words.manifest }}</h3><pre>{{ JSON.stringify(manifestForDisplay(review.manifest), null, 2) }}</pre>
        <h3 class="mt-5">{{ words.changes }}</h3><p v-if="!Object.keys(review.adapter_files).length" class="help">{{ words.unchanged }}</p>
        <div v-for="(code, path) in review.adapter_files" :key="path"><h4 class="break-all">{{ path }}</h4><pre>{{ code }}</pre></div>
        <template v-if="review.status === 'reviewable' && !review.archived_at"><label class="check mt-4"><input v-model="confirmed" type="checkbox" data-testid="project-confirm">{{ words.check }}</label>
          <p v-if="!confirmed" class="help mt-2" data-testid="project-approve-hint">{{ words.approveHint }}</p>
          <button class="btn primary mt-3" :disabled="busy || !confirmed || !review.public_test.passed || locked.has('approve:'+review.id)" data-testid="project-approve" @click="approve">{{ words.approve }}</button>
          <p v-if="!review.public_test.passed" class="help mt-2" data-testid="project-approve-blocked">{{ words.approveNeedsTest }}</p></template>
        <form class="mt-6" @submit.prevent="action(async () => { await portal('evidence', { revision_id: review!.id, notes, code_url: codeUrl }) })">
          <h3>{{ words.evidence }}</h3><p class="help">{{ words.evidenceHelp }}</p>
          <label class="field"><span>{{ words.notes }}</span><textarea v-model="notes" maxlength="8000" rows="5"></textarea></label>
          <label class="field"><span>{{ words.codeUrl }}</span><input v-model="codeUrl" type="url" maxlength="1000"></label>
          <button class="btn sm" :disabled="busy">{{ words.saveEvidence }}</button>
        </form><button class="btn sm mt-4" @click="review = null">{{ words.close }}</button>
      </section>
      <section class="panel mb-6" data-testid="project-evaluate">
        <h2 id="evaluate">{{ words.step3 }}</h2><p class="help">{{ words.step3Note }}</p>
        <p v-if="!openPhases.length" class="help">{{ words.phaseUnavailable }}</p>
        <template v-else>
          <p class="mt-4" data-testid="evaluation-quota">{{ selectedPhase ? pick(selectedPhase.phases.name_en, selectedPhase.phases.name_zh) : '' }}<template v-if="dailyLimit != null">
            · <strong data-testid="evaluation-daily-limit">{{ pick(`${words.dailyLimit}: ${dailyLimit}`, `${words.dailyLimit} ${dailyLimit} 次`) }}</strong></template><template v-if="quota">
            · <strong>{{ pick(`${words.left}: ${quota.remaining}`, `${words.left} ${quota.remaining} 次`) }}</strong></template>
            · <strong data-testid="evaluation-active-count">{{ pick(`${words.concurrent}: ${runningNow} ${words.concurrentOf} ${activeLimit}`, `${words.concurrent} ${runningNow}${words.concurrentOf}${activeLimit}`) }}</strong>
            <span v-if="dailyLimit != null" class="help" data-testid="evaluation-reset"> ({{ formatDailyReset(quota?.resets_at, locale) }})</span></p>
          <p v-if="quota && quota.remaining <= 0" class="help">{{ words.noneLeft }}</p>
          <p v-else-if="activeBatch" class="help" data-testid="evaluation-active-limit">{{ words.active.replace('{n}', String(activeLimit)) }}</p>
          <p v-if="approvedVersions.length" class="help mt-3" data-testid="self-check-note">{{ words.selfCheckNote }}<template v-if="!canSelfCheck(quota)"> {{ words.selfCheckNeed }}</template></p>
          <label v-if="approvedVersions.length" class="check no-model-option mt-3" :title="words.noModelHelp" data-testid="evaluation-no-model">
            <input v-model="noModel" type="checkbox" name="observer-no-model" :disabled="busy" data-testid="evaluation-no-model-input">{{ words.noModel }}</label>
          <p v-if="approvedVersions.length" class="help no-model-help" :class="{ on: noModel }" data-testid="evaluation-no-model-help">{{ noModel ? words.noModelOn + ' ' : '' }}{{ words.noModelHelp }}</p>
          <p v-if="!approvedVersions.length" class="text3 mt-3">{{ words.noApproved }}</p>
          <div v-for="v in approvedVersions" :key="v.revision.id" class="flex flex-wrap items-center gap-3 mt-3" :data-revision-id="v.revision.id">
            <span>{{ v.title }}</span>
            <span v-if="finalRole(finalVersion, v.revision.id)" class="pill ok">{{ words.finalBadge }}</span>
            <span v-if="v.revision.approved_at" class="meta">{{ words.confirmedAt }} {{ when(v.revision.approved_at) }}</span>
            <span v-if="v.evaluated" class="meta">{{ pick(`${words.evaluated} ${v.evaluated}${words.times}`, `${words.evaluated} ${v.evaluated} ${words.times}`) }}</span>
            <button type="button" class="btn primary sm" :disabled="busy || locked.has('evaluate:'+v.revision.id) || !selectedPhase?.projects_enabled || activeBatch || (quota != null && quota.remaining <= 0)" data-testid="project-evaluate-button" :title="blockText(evalBlocked)" :aria-busy="pending === 'evaluate:'+v.revision.id" @click="evaluate(v.revision.id)">{{ pending === 'evaluate:'+v.revision.id ? words.working : v.evaluated ? words.evaluateAgain : words.evaluate }}</button>
            <button type="button" class="btn sm" :disabled="busy || locked.has('selfcheck:'+v.revision.id) || !selectedPhase?.projects_enabled || activeBatch || selfCheckActive || !canSelfCheck(quota)" :title="selfCheckBlocked ? blockText(selfCheckBlocked) : words.selfCheckNote" data-testid="project-self-check-button" :aria-busy="pending === 'selfcheck:'+v.revision.id" @click="selfCheck(v.revision.id)">{{ pending === 'selfcheck:'+v.revision.id ? words.working : words.selfCheck }}</button>
          </div>
          <p v-if="approvedVersions.length && (evalBlocked || selfCheckBlocked) && evalBlocked !== 'busy'" class="help mt-3" role="status" data-testid="evaluation-disabled-reason">
            {{ words.blocked }}{{ pick(': ', '：') }}{{ blockText(evalBlocked ?? selfCheckBlocked) }}<template v-if="!evalBlocked && selfCheckBlocked"> ({{ words.selfCheck }})</template></p>
        </template>
      </section>
      <section v-if="finalVersion" class="panel mb-6" data-testid="final-version">
        <h2 id="final">{{ words.final }}</h2>
        <p class="help">{{ words.finalIntro }}</p>
        <p class="help">{{ words.finalDefault }}<template v-if="finalVersion.deadline && !finalVersion.locked"> {{ words.finalDeadline }} {{ when(finalVersion.deadline) }}.</template></p>
        <p v-if="relayFinalRisk" class="errors mt-3" role="note" data-testid="final-version-relay-warning">{{ words.finalRelay }} <a href="#model-api">{{ t('submit.model_api.title') }}</a></p>
        <p v-if="finalVersion.locked" class="mt-3" role="status" data-testid="final-version-locked">{{ words.finalLocked }}</p>
        <p v-if="!finalVersion.revision_id" class="text3 mt-3">{{ words.finalNone }}</p>
        <p v-else class="mt-3 flex flex-wrap items-center gap-3" data-testid="final-version-current">
          <strong>{{ titles.get(finalVersion.revision_id) ?? finalVersion.revision_id }}</strong>
          <span class="pill ok">{{ finalVersion.source === 'chosen' ? words.finalChosen : words.finalBest }}</span>
          <span v-if="finalVersion.chosen_at && finalVersion.source === 'chosen'" class="meta">{{ when(finalVersion.chosen_at) }}</span>
          <span v-else-if="finalVersion.best_score != null" class="meta">{{ words.finalScore }} {{ finalVersion.best_score.toFixed(2) }}</span>
          <button v-if="canClearFinal(finalVersion)" type="button" class="btn sm" :disabled="busy || locked.has('final:clear')" data-testid="final-version-clear" @click="setFinal(null)">{{ words.finalClear }}</button>
        </p>
        <div v-for="v in finalCandidates" :key="v.revision.id" class="flex flex-wrap items-center gap-3 mt-3" :data-final-revision-id="v.revision.id">
          <span>{{ v.title }}</span>
          <span v-if="v.revision.approved_at" class="meta">{{ words.confirmedAt }} {{ when(v.revision.approved_at) }}</span>
          <span v-if="finalRole(finalVersion, v.revision.id)" class="pill ok">{{ words.finalBadge }}</span>
          <button v-if="canChooseFinal(finalVersion, v.revision.id)" type="button" class="btn sm" :disabled="busy || locked.has('final:'+v.revision.id)" data-testid="final-version-set" @click="setFinal(v.revision.id)">{{ words.finalSet }}</button>
        </div>
      </section>
      <section class="panel mb-6"><h2 id="results">{{ words.batches }}</h2>
        <p v-if="!listedBatches.length" class="text3 mt-3">{{ words.noBatches }}</p>
        <template v-for="b in listedBatches" :key="b.id">
        <article v-if="firstOfGroup(b) && repeats.get(b.repeat_group!)" class="project-row repeat-summary" data-testid="self-check-summary">
          <p><strong>{{ words.selfCheckTitle }}</strong><template v-if="b.revision_id && titles.get(b.revision_id)"> · {{ titles.get(b.revision_id) }}</template>
            · {{ words.selfCheckDone.replace('{done}', String(repeats.get(b.repeat_group!)!.scored)).replace('{total}', String(repeats.get(b.repeat_group!)!.runs)) }}
            <span class="pill info ml-2">{{ words.selfCheckOff }}</span>
            <span v-if="isNoModel(b)" class="pill no-model ml-2" data-testid="self-check-no-model">{{ words.noModelPill }}</span></p>
          <p v-if="repeats.get(b.repeat_group!)!.overall" class="mt-2" data-testid="self-check-overall">{{ words.selfCheckOverall }}: <strong>{{ fmt2(repeats.get(b.repeat_group!)!.overall!.mean) }}</strong>
            <span class="meta">({{ fmt2(repeats.get(b.repeat_group!)!.overall!.min) }}–{{ fmt2(repeats.get(b.repeat_group!)!.overall!.max) }})</span></p>
          <template v-if="repeats.get(b.repeat_group!)!.cards.length"><p class="meta mt-2">{{ words.selfCheckCards }}</p>
          <div class="repeat-cards"><span v-for="c in repeats.get(b.repeat_group!)!.cards" :key="c.scenario_id" class="repeat-card" data-testid="self-check-card">
            <span class="m text-sm">{{ scenarioNames[c.scenario_id] ? scenarioLabel(scenarioNames[c.scenario_id]!.slug, scenarioNames[c.scenario_id]!.name, locale) : '—' }}</span>
            <strong>{{ fmt2(c.mean) }}</strong> <span class="meta">({{ fmt2(c.min) }}–{{ fmt2(c.max) }})</span></span></div></template>
        </article>
        <article :id="'batch-'+b.id" class="project-row" :class="{ target: b.id === targetBatch, 'repeat-member': !!b.repeat_group, 'latest-failed': failure?.batch.id === b.id }">
          <p>{{ when(b.created_at) }}<template v-if="b.revision_id && titles.get(b.revision_id)"> · {{ titles.get(b.revision_id) }}</template><template v-if="phaseName(b.phase_id)"> · {{ phaseName(b.phase_id) }}</template> · {{ statuses[b.status] ?? b.status }}
            <span v-if="failure?.batch.id === b.id" class="pill failed ml-2" data-testid="batch-latest-failed">{{ words.latestPill }}</span>
            <span v-if="b.quota_refunded" class="pill info ml-2" data-testid="batch-refunded">{{ words.refunded }}</span>
            <span v-if="isNoModel(b)" class="pill no-model ml-2" :title="words.noModelHelp" data-testid="batch-no-model">{{ words.noModelPill }}</span>
            <span v-if="b.repeat_group && repeatIndex(b) > 0" class="pill ml-2" data-testid="batch-self-check">{{ words.selfCheckOne.replace('{n}', String(repeatIndex(b))).replace('{total}', String(b.repeat_runs ?? SELF_CHECK_RUNS)) }}</span></p>
          <p v-if="b.score != null">{{ words.average }}: {{ b.score.toFixed(2) }}</p>
          <p v-if="b.observer_runs.filter(r => r.result_path).length > 1" class="flex flex-wrap items-center gap-3 mt-3">
            <button type="button" class="btn sm" :disabled="!!zipProgress[b.id]" data-testid="download-all-results" @click="downloadAllResults(b)">{{ zipProgress[b.id] ? words.downloadAllProgress.replace('{done}', String(zipProgress[b.id]!.done)).replace('{total}', String(zipProgress[b.id]!.total)) : words.downloadAll }}</button>
            <span v-if="zipOutcome[b.id]" :class="zipOutcome[b.id]!.failed ? 'errors' : ''" role="status" data-testid="download-all-outcome">{{ zipOutcome[b.id]!.text }}</span>
          </p>
          <div v-for="run in sortedRuns(b.observer_runs)" :key="run.id" class="flex flex-wrap gap-3 mt-3 items-center">
            <span v-if="scenarioNames[run.scenario_id]" class="m text-sm" :title="scenarioNames[run.scenario_id]!.slug" data-testid="run-scenario">{{ scenarioLabel(scenarioNames[run.scenario_id]!.slug, scenarioNames[run.scenario_id]!.name, locale) }}</span>
            <span class="pill">{{ statuses[run.status] ?? run.status }}</span>
            <span v-if="run.score != null">{{ run.score_summary?.calibration ? t('leaderboard.calibrated_score') + ': ' : '' }}{{ run.score.toFixed(2) }}</span>
            <span v-if="run.score_summary?.raw_score" class="meta">{{ t('leaderboard.raw_score') }}: {{ run.score_summary.raw_score.total.toFixed(2) }}</span>
            <button v-if="run.result_path" class="btn sm" :disabled="busy" @click="download(run.id)">{{ words.download }}</button>
            <button type="button" class="btn sm" :aria-expanded="openLogs === 'run:'+run.id" data-testid="run-logs-button" @click="toggleLogs('run:'+run.id)">{{ words.logs }}</button>
            <RunLogs v-if="openLogs === 'run:'+run.id" :target="{ run_id: run.id }" :label="scenarioNames[run.scenario_id] ? scenarioLabel(scenarioNames[run.scenario_id]!.slug, scenarioNames[run.scenario_id]!.name, locale) : when(b.created_at)" :file-stem="cardFolder(run)" :statuses="statuses" @close="openLogs = ''" />
          </div>
        </article>
        </template>
      </section>
      <p class="help mt-6">{{ pick('Bring your own model API; organizer credits are not provided. Awards require LLM-driven agent techniques in at least two stages (see Rules). Never include a permanent key in your repository or ZIP.','请自备模型 API，平台不提供额度。评奖要求至少两个环节采用大模型驱动的智能体技术（见规则）。不要把永久密钥放进仓库或 ZIP。') }}</p>
    </template>
  </section>
</template>

<style scoped>
h2 { font-size: 1.2rem; font-weight: 600; } h3 { font-weight: 600; }
.project-row { padding: 1rem 0; border-bottom: 1px solid #333; }
.project-row.target { outline: 1px solid #315efb; outline-offset: .25rem; }
.repeat-summary { border-left: 2px solid #315efb; padding-left: .75rem; }
.repeat-member { padding-left: .75rem; border-left: 2px solid #333; }
.repeat-cards { display: flex; flex-wrap: wrap; gap: .5rem 1.25rem; margin-top: .35rem; }
.repeat-card { display: inline-flex; align-items: baseline; gap: .4rem; }
/* A quiet text link (the withdrawn-versions toggle). */
.log-link { margin-left: auto; background: none; border: 0; padding: .25rem 0; font-size: .75rem; color: #858585; text-decoration: underline; text-underline-offset: 3px; cursor: pointer; }
.meta { font-size: .8rem; color: #858585; }
.model-api > summary { cursor: pointer; list-style: none; display: flex; flex-wrap: wrap; align-items: baseline; gap: .5rem 1rem; }
.model-api > summary::-webkit-details-marker { display: none; }
.model-api > summary::after { content: '+'; margin-left: auto; color: #78a6ff; }
.model-api[open] > summary::after { content: '–'; }
.model-api-title { font-size: 1.2rem; font-weight: 600; }
.model-api-hint { margin: 0; }
.model-api-configured { font-size: .85rem; color: #bdbdbd; overflow-wrap: anywhere; }
.latest-failure { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem .9rem; padding: .75rem 1rem; border: 1px solid #a33a45; background: #1a0c0f; }
.project-row.latest-failed { border-left: 3px solid #e5484d; padding-left: .75rem; }
.model-api-body { margin-top: .75rem; }
.upload-progress { display: block; width: 100%; max-width: 24rem; height: .5rem; margin-top: .35rem; accent-color: #315efb; }
.log-link:hover { color: #bdbdbd; } .log-link:disabled { opacity: .5; cursor: default; }
pre { max-height: 24rem; overflow: auto; padding: 1rem; margin-top: .5rem; background: #0b0b0b; font-size: .8rem; white-space: pre-wrap; overflow-wrap: anywhere; }
/* ─── Simplified layout (v2) ─── */
.cw-alerts { display: grid; gap: .5rem; }
.cw-alerts:empty { display: none; }
.cw-notice { padding: .5rem .9rem; border: 1px solid #1f5a38; color: #bfe9cf; background: #0b1610; }
.cw-progress { border: 1px solid #2a2f45; background: linear-gradient(180deg, #0f1324, #0b0d16); margin-top: 1rem; }
.cw-progress-head { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem .9rem; padding: .9rem 1.25rem; border-bottom: 1px solid #2a2f45; }
.cw-grow-left { margin-left: auto; }
.cw-steps { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); list-style: none; margin: 0; padding: 0; }
.cw-step { position: relative; display: flex; flex-direction: column; gap: .15rem; padding: .9rem 1.25rem; border-right: 1px solid #2a2f45; min-width: 0; }
.cw-step:last-child { border-right: 0; }
.cw-step.current { background: #121a3a; }
.cw-step.current::before { content: ''; position: absolute; left: 0; right: 0; top: 0; height: 2px; background: #315efb; }
.cw-step-n { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .72rem; color: #858585; }
.cw-step.done .cw-step-n { color: #59d78d; }
.cw-step-t { font-weight: 600; }
.cw-step-s { font-size: .8rem; color: #a9adbd; overflow-wrap: anywhere; }
.cw-next { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem 1rem; padding: .9rem 1.25rem; border-top: 1px solid #2a2f45; background: #0c1020; }
.cw-next[data-kind="failed"] { background: #1a0c0f; border-top-color: #7a2a2a; }
.cw-next-label { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .7rem; letter-spacing: .1em; color: #78a6ff; text-transform: uppercase; }
.cw-next-text { flex: 1 1 18rem; min-width: 0; }
.cw-next-actions { display: flex; flex-wrap: wrap; gap: .5rem; }
.cw-quota { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid #2a2f45; margin-top: 1rem; background: #0d0f17; }
.cw-q { display: flex; flex-direction: column; gap: .2rem; padding: .75rem 1rem; border-right: 1px solid #2a2f45; min-width: 0; }
.cw-q:last-child { border-right: 0; }
.cw-q-k { font-size: .78rem; color: #858585; }
.cw-q-v { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 1.25rem; font-weight: 600; }
.cw-q-v small { font-size: .75rem; color: #858585; font-weight: 400; }
.cw-q-reset { font-size: .85rem; color: #d0d3de; }
.cw-bar { display: block; height: 3px; background: #1d2130; }
.cw-bar i { display: block; height: 3px; background: #315efb; }
.cw-tabs { display: flex; gap: .25rem; margin-top: 1.5rem; border-bottom: 1px solid #2a2f45; overflow-x: auto; }
.cw-tab { padding: .55rem 1rem; color: #858585; background: none; border: 1px solid transparent; border-bottom: 0; white-space: nowrap; cursor: pointer; font-size: .95rem; }
.cw-tab.on { color: #fff; background: #0d0f17; border-color: #2a2f45; margin-bottom: -1px; }
.cw-row-head { display: flex; flex-wrap: wrap; align-items: center; gap: .4rem .9rem; }
.cw-link { background: none; border: 0; padding: 0; color: #78a6ff; cursor: pointer; font: inherit; }
.cw-link:disabled { opacity: .5; cursor: default; }
.cw-upload { border: 1px dashed #2d3450; padding: 1rem 1.1rem; margin-top: 1rem; }
.cw-table { margin-top: 1rem; font-size: .9rem; }
.cw-tr { display: grid; grid-template-columns: minmax(0, 2.2fr) minmax(0, .9fr) minmax(0, 1fr) minmax(0, .7fr) minmax(0, 2.4fr); gap: .75rem; align-items: center; padding: .7rem .25rem; border-bottom: 1px solid #1b1f2c; }
.cw-th { font-size: .75rem; color: #858585; padding-top: 0; }
.cw-right { text-align: right; }
.cw-block { display: block; }
.cw-score { font-family: 'IBM Plex Mono', ui-monospace, monospace; }
.cw-score.best { color: #59d78d; }
.cw-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: .4rem; }
.cw-menu { position: relative; }
.cw-menu > summary { list-style: none; }
.cw-menu > summary::-webkit-details-marker { display: none; }
.cw-menu-list { position: absolute; right: 0; top: calc(100% + 4px); z-index: 5; min-width: 12rem; display: flex; flex-direction: column; padding: .25rem 0; background: #121522; border: 1px solid #2a2f45; box-shadow: 0 8px 24px #0009; }
.cw-menu-list button { text-align: left; padding: .45rem .9rem; background: none; border: 0; color: #e9ebf2; cursor: pointer; font-size: .85rem; }
.cw-menu-list button:hover { background: #1a1f33; }
.cw-menu-list button:disabled { color: #666; cursor: default; }
.cw-menu-list .cw-danger { color: #ff8a8a; border-top: 1px solid #2a2f45; }
.cw-sub { padding: .5rem .25rem 1rem; border-bottom: 1px solid #1b1f2c; }
.cw-review { border-left: 2px solid #315efb; padding-left: 1rem; outline: none; }
.cw-batch { padding: .25rem 0; border-bottom: 1px solid #1b1f2c; }
.cw-batch.latest-failed { border-left: 3px solid #e5484d; padding-left: .6rem; }
.cw-batch.target { outline: 1px solid #315efb; outline-offset: .25rem; }
.cw-batch-line { margin-top: .5rem; }
.pill.no-model { border-color: #c9a227; color: #f3d58a; }
.no-model-option { display: inline-flex; align-items: center; gap: .4rem; }
.no-model-help { font-size: .8rem; margin-top: .25rem; }
.no-model-help.on { color: #f3d58a; }
.cw-batch-toggle { width: 100%; display: grid; grid-template-columns: 1rem 9rem minmax(0, 1fr) auto 6rem; gap: .6rem; align-items: center; padding: .65rem .25rem; background: none; border: 0; color: inherit; text-align: left; cursor: pointer; font: inherit; }
.cw-batch-toggle:hover { background: #10131d; }
.cw-batch-pills { display: flex; flex-wrap: wrap; gap: .35rem; justify-content: flex-end; }
.cw-batch-body { padding: 0 .25rem 1rem 1.75rem; }
.cw-caret { color: #858585; }
.cw-cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .6rem; margin-top: .75rem; }
.cw-card { display: flex; flex-direction: column; gap: .15rem; padding: .6rem .8rem; border: 1px solid #2a2f45; background: #121522; min-width: 0; }
.cw-card.failed { border-color: #7a2a2a; }
.cw-card.failed .cw-card-score { color: #ff6b6b; }
.cw-card-score { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 1.05rem; }
.cw-card-links { display: flex; gap: .8rem; font-size: .8rem; }
@media (max-width: 720px) {
  .cw-progress-head, .cw-next { padding: .75rem .9rem; }
  .cw-step { padding: .6rem .4rem; text-align: center; align-items: center; }
  .cw-step-s { display: none; }
  .cw-step-t { font-size: .8rem; }
  .cw-quota { grid-template-columns: 1fr 1fr; }
  .cw-q:nth-child(2) { border-right: 0; }
  .cw-q:nth-child(-n+2) { border-bottom: 1px solid #2a2f45; }
  .cw-th { display: none; }
  .cw-tr { grid-template-columns: minmax(0, 1fr) auto; gap: .35rem .6rem; padding: .8rem .1rem; }
  .cw-tr > .cw-td-name { grid-column: 1 / 2; }
  .cw-tr > .cw-evals, .cw-tr > .cw-score { font-size: .8rem; color: #a9adbd; }
  .cw-actions { grid-column: 1 / -1; justify-content: flex-start; }
  .cw-batch-toggle { grid-template-columns: 1rem minmax(0, 1fr) auto; }
  .cw-batch-toggle > .meta { grid-column: 2 / 4; order: -1; }
  .cw-batch-pills { grid-column: 2 / 3; justify-content: flex-start; }
  .cw-batch-body { padding-left: .5rem; }
  .cw-cards { grid-template-columns: 1fr 1fr; }
  .cw-menu-list { right: auto; left: 0; }
}
</style>
