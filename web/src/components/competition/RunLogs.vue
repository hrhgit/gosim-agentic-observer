<script setup lang="ts">
// Logs of one run (one card) or one version, opened right under its row: the agent's
// own output (agent.log, scrubbed by the runner) and the platform's step diagnostics.
import { computed, onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { portal } from '../../lib/observerPortal'
import { triggerDownload } from '../../lib/storage'
import { bytes } from '../../lib/format'
import { agentLogFileName, loadLogs, type AgentLogView, type LogTarget, type Logs } from '../../lib/runLogs'

const props = defineProps<{ target: LogTarget; label: string; fileStem: string; statuses: Record<string, string> }>()
const emit = defineEmits<{ close: [] }>()
const { pick } = useI18n()
const logs = ref<Logs | null>(null), failed = ref(''), downloading = ref(false), downloadError = ref('')
const isRun = computed(() => 'run_id' in props.target)
const agent = computed<AgentLogView | null>(() => logs.value?.agent ?? null)
const w = computed(() => pick({
  title: 'Logs', loading: 'Loading logs…', failed: 'Logs could not be loaded', retry: 'Try again', close: 'Close',
  privacy: 'Visible only to your team and the organizers. Values of your secret team variables and platform credentials are replaced with [REDACTED].',
  agentRun: 'Agent output (agent.log)', agentVersion: 'Public test: agent output (agent.log, with build output)',
  agentHelp: 'Your program\'s stderr (and build output), exactly what your agent printed. Log to stderr; stdout is the protocol.',
  tail: 'Showing the last part only', download: 'Download full agent.log', downloading: 'Downloading…',
  noAgentRun: 'No agent.log for this run: it is still running, or it failed. For a failed run, the last part of your program\'s output is in the platform diagnostics below. A finished run also has the full output in its result ZIP ("Download result"): agent.log in the card folder.',
  noAgentVersion: 'No public test output for this version yet. If preparation failed, the build output is in the platform diagnostics below. After evaluation, every card\'s agent.log is under "Logs" next to it in Evaluation results, and in its result ZIP.',
  empty: '(agent.log is empty: your program printed nothing to stderr)',
  platform: 'Platform diagnostics', platformHelp: 'Status of each platform step (prepare, run, score). A failed step includes the last part of the program output.',
  noDiagnostics: 'No platform diagnostics yet.',
  kinds: { prepare: 'Prepare', execute: 'Run', engine: 'Run (engine)', score: 'Score' } as Record<string, string>,
  steps: { succeeded: 'Succeeded', failed: 'Failed', running: 'Running', queued: 'Queued', claimed: 'Running', cancelled: 'Cancelled' } as Record<string, string>,
}, {
  title: '日志', loading: '正在读取日志…', failed: '日志读取失败', retry: '重试', close: '关闭',
  privacy: '只供本队与主办方查看。团队密钥变量的值和平台凭证已替换为 [REDACTED]。',
  agentRun: '智能体输出（agent.log）', agentVersion: '公开测试的智能体输出（agent.log，含编译输出）',
  agentHelp: '你的程序写到 stderr 的内容（以及编译输出），原样保留。日志请写 stderr，stdout 只用于协议消息。',
  tail: '只显示最后一部分', download: '下载完整 agent.log', downloading: '正在下载…',
  noAgentRun: '这次运行没有 agent.log：可能还在运行，或运行失败。运行失败时，程序输出的最后部分在下面的平台诊断里。正常结束的运行，完整输出也在结果 ZIP 里（点「下载结果」，每张卡片文件夹中的 agent.log）。',
  noAgentVersion: '这个版本还没有公开测试输出。准备失败时，编译输出在下面的平台诊断里。正式评测后，每张卡片的 agent.log 在「评测结果」里该卡片旁的「查看日志」中，也在它的结果 ZIP 里。',
  empty: '（agent.log 为空：程序没有向 stderr 输出内容）',
  platform: '平台诊断', platformHelp: '平台各步骤（准备、运行、评分）的状态；失败的步骤附带程序输出的最后部分。',
  noDiagnostics: '暂时没有平台诊断。',
  kinds: { prepare: '准备', execute: '运行', engine: '运行（评测引擎）', score: '评分' } as Record<string, string>,
  steps: { succeeded: '成功', failed: '失败', running: '运行中', queued: '排队中', claimed: '运行中', cancelled: '已取消' } as Record<string, string>,
}))

async function load() {
  failed.value = ''
  try { logs.value = await loadLogs(portal, props.target) }
  catch (e) { failed.value = e instanceof Error ? e.message : String(e) }
}
async function downloadFull() {
  const run = logs.value?.agentRun
  if (!run || downloading.value) return
  downloading.value = true; downloadError.value = ''
  try {
    const full = await portal<AgentLogView>('agent_log', { run_id: run, full: true })
    if (!full.available) throw new Error('agent_log_unavailable')
    triggerDownload(new Blob([full.log ?? ''], { type: 'text/plain;charset=utf-8' }), agentLogFileName(props.fileStem))
  } catch (e) { downloadError.value = e instanceof Error ? e.message : String(e) }
  finally { downloading.value = false }
}
onMounted(load)
</script>

<template>
  <section class="run-logs" data-testid="run-logs" aria-live="polite">
    <div class="head">
      <h3>{{ w.title }} · <span class="label">{{ label }}</span></h3>
      <button type="button" class="btn sm" data-testid="run-logs-close" @click="emit('close')">{{ w.close }}</button>
    </div>
    <p class="help">{{ w.privacy }}</p>
    <p v-if="!logs && !failed" class="help mt-3" role="status">{{ w.loading }}</p>
    <p v-if="failed" class="errors mt-3" role="alert">{{ w.failed }} ({{ failed }}) <button type="button" class="btn sm ml-2" @click="load">{{ w.retry }}</button></p>
    <template v-if="logs">
      <h4 class="mt-4">{{ isRun ? w.agentRun : w.agentVersion }}</h4>
      <template v-if="agent?.available">
        <p class="help">{{ w.agentHelp }}</p>
        <p class="flex flex-wrap items-center gap-3 mt-2">
          <span v-if="agent.truncated" class="meta" data-testid="agent-log-tail">{{ w.tail }} · {{ bytes(agent.bytes ?? 0) }}</span>
          <span v-else-if="agent.bytes" class="meta">{{ bytes(agent.bytes) }}</span>
          <button type="button" class="btn sm" :disabled="downloading" data-testid="agent-log-download" @click="downloadFull">{{ downloading ? w.downloading : w.download }}</button>
          <span v-if="downloadError" class="errors" role="status">{{ downloadError }}</span>
        </p>
        <pre data-testid="agent-log">{{ agent.log || w.empty }}</pre>
      </template>
      <p v-else class="help mt-2" data-testid="agent-log-missing">{{ isRun ? w.noAgentRun : w.noAgentVersion }}</p>
      <h4 class="mt-5">{{ w.platform }}</h4>
      <p class="help">{{ w.platformHelp }}</p>
      <p v-if="!logs.diagnostics.length" class="help mt-2">{{ w.noDiagnostics }}</p>
      <article v-for="(entry, index) in logs.diagnostics" :key="index" class="mt-3" data-testid="project-diagnostics">
        <p class="text-sm">{{ w.kinds[entry.kind] ?? entry.kind }} · {{ w.steps[entry.status] ?? statuses[entry.status] ?? entry.status }}<template v-if="entry.code && entry.code !== entry.status && entry.code !== 'completed'"> · <span class="m">{{ entry.code }}</span></template></p>
        <pre v-if="entry.log">{{ entry.log }}</pre>
      </article>
    </template>
  </section>
</template>

<style scoped>
.run-logs { flex-basis: 100%; width: 100%; min-width: 0; margin-top: .75rem; padding: .9rem 1rem; border: 1px solid #315efb; border-radius: 4px; background: #111; }
.head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .5rem; }
.label { font-weight: 400; overflow-wrap: anywhere; text-transform: none; }
.run-logs h3, .run-logs .btn { text-transform: none; }
h3 { font-weight: 600; } h4 { font-weight: 600; font-size: .95rem; }
.meta { font-size: .8rem; color: #858585; }
pre { max-height: 24rem; overflow: auto; padding: .75rem; margin-top: .5rem; background: #0b0b0b; font-size: .78rem; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
