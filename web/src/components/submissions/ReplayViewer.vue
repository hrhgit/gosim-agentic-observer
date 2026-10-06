<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { readObjectText } from '../../lib/storage'
import { REPLAY_PLAYBACK, REPLAY_SEEK, cursorForRound, embedReplayHtml, isStaleReport, readPositionMessage, roundForCursor, type ReplayPlayback } from '../../lib/replayEmbed'

/**
 * Loads the organizer-style decision_replay.html from the private results bucket and shows it in a sandboxed iframe.
 * `cursor` is the position shared with the observed-sky map (actions shown so far, out of `actions`): the replay
 * seeks to it when it loads or when the map moves, and reports its own rounds back through `update:cursor`.
 * The home page's demo passes `source` (a bundled replay) instead of `path`, loads it straight away with `autoload`,
 * and sets the speed / autoplay / loop through `playback`.
 */
const props = defineProps<{
  path?: string; source?: () => Promise<string>; autoload?: boolean; playback?: ReplayPlayback | null
  note?: string; cursor?: number | null; actions?: number
}>()
const emit = defineEmits<{ 'update:cursor': [cursor: number] }>()
const { t } = useI18n()
const html = ref<string | null>(null)
const srcdoc = computed(() => html.value ? embedReplayHtml(html.value) : '')
const frame = ref<HTMLIFrameElement | null>(null)
let rounds = 0
let reported: number | null = null
let seq = 0
const loading = ref(false)
const failed = ref(false)
let blobUrl: string | null = null

async function open() {
  if (html.value || loading.value) return
  loading.value = true
  failed.value = false
  try { html.value = props.source ? await props.source() : await readObjectText('results', props.path ?? '') }
  catch { failed.value = true }
  finally { loading.value = false }
}
function close() { html.value = null; rounds = 0; reported = null; seq = 0 }
function onLoad() {
  seek(props.cursor)
  if (props.playback) frame.value?.contentWindow?.postMessage({ type: REPLAY_PLAYBACK, ...props.playback }, '*')
}
function seek(cursor: number | null | undefined) {
  if (cursor == null || !frame.value?.contentWindow) return
  frame.value.contentWindow.postMessage({ type: REPLAY_SEEK, seq: ++seq, round: roundForCursor(cursor, rounds || props.actions || 0) }, '*')
}
function onMessage(e: MessageEvent) {
  if (!frame.value || e.source !== frame.value.contentWindow) return
  const msg = readPositionMessage(e.data)
  if (!msg || isStaleReport(msg, seq)) return
  rounds = msg.rounds
  reported = cursorForRound(msg.round, props.actions || msg.rounds)
  emit('update:cursor', reported)
}
window.addEventListener('message', onMessage)
// The map moved (its slider or its own playback): bring the replay along, unless the replay itself reported this position.
watch(() => props.cursor, c => { if (c != null && c !== reported) { reported = null; seek(c) } })
function openTab() {
  if (!html.value) return
  if (blobUrl) URL.revokeObjectURL(blobUrl)
  blobUrl = URL.createObjectURL(new Blob([html.value], { type: 'text/html' }))
  window.open(blobUrl, '_blank', 'noopener')
}
onMounted(() => { if (props.autoload) void open() })
onUnmounted(() => { window.removeEventListener('message', onMessage); if (blobUrl) URL.revokeObjectURL(blobUrl) })
</script>

<template>
  <div class="replay-viewer" data-testid="replay-viewer">
    <p class="actions-inline">
      <span v-if="autoload && !html && !failed" class="text3 text-sm">{{ t('common.loading') }}</span>
      <button v-if="!html && (!autoload || failed)" type="button" class="btn sm primary" :disabled="loading" data-testid="replay-open" @click="open">{{ loading ? t('common.loading') : t('subs.replay.open') }} ▶</button>
      <template v-else>
        <button type="button" class="btn sm" data-testid="replay-new-tab" @click="openTab">{{ t('subs.replay.new_tab') }} ↗</button>
        <button v-if="!autoload" type="button" class="btn sm" @click="close">{{ t('common.close') }}</button>
      </template>
      <span v-if="failed" class="text-sm text-[#ff6b6b]">{{ t('subs.download_failed') }}</span>
      <span class="text3 text-xs">{{ note ?? t('subs.replay.note') }}</span>
    </p>
    <div v-if="html" class="replay-frame-wrap mt-4">
      <iframe class="replay-frame" sandbox="allow-scripts" ref="frame" :srcdoc="srcdoc" :title="t('subs.replay.title')" data-testid="replay-frame" @load="onLoad"></iframe>
    </div>
  </div>
</template>

<style scoped>
.replay-frame-wrap { border: 1px solid rgba(255,255,255,.25); background: #000; }
.replay-frame { display: block; width: 100%; height: 820px; border: 0; background: #000; }
</style>
