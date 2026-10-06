<script setup lang="ts">
// One popup per new waiting team request/invitation: "N requests are waiting for you" with a button to the team
// page, where they are answered. Each row pops up once (lib/popupRules; remembered locally and on the server); the
// badge and the team-page block keep showing it until it is answered. One popup per page load (stores/overlay).
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from '../../composables/useI18n'
import { supabase } from '../../lib/supabase'
import { TEAM_INBOX_SEEN_KEY, actionable, normalizeInbox, parseSeen, type InboxRow } from '../../lib/teamInbox'
import { inboxKey } from '../../lib/popupRules'
import { markSeen, seenKeys, useSeenWhileOpen } from '../../stores/popupSeen'
import { overlayActive, releaseOverlay, requestOverlay } from '../../stores/overlay'
import { pendingTeamActions } from '../../stores/teamNotifications'

const OVERLAY = 'team-requests'
const { pick } = useI18n()
const route = useRoute(), router = useRouter()
const dialog = ref<HTMLDialogElement | null>(null)
const rows = ref<InboxRow[]>([])
const open = computed(() => rows.value.length > 0 && overlayActive(OVERLAY))
const requests = computed(() => rows.value.filter(r => r.kind === 'request'))
let checking = false

function readSeen() { try { return localStorage.getItem(TEAM_INBOX_SEEN_KEY) } catch { return null } }

watch([pendingTeamActions, () => route.path], async ([count, path]) => {
  // Already on the page that lists them, or a popup already queued.
  if (!count || path === '/team' || path === '/notifications' || rows.value.length || checking) return
  checking = true
  try {
    const { data, error } = await supabase.rpc('my_team_inbox')
    if (error) return
    const waiting = actionable(normalizeInbox(data))
    const legacy = parseSeen(readSeen())
    markSeen(waiting.filter(r => legacy.has(r.id)).map(r => inboxKey(r.id)))
    const seen = await seenKeys()
    const fresh = waiting.filter(r => !seen.has(inboxKey(r.id)))
    if (!fresh.length) return
    rows.value = fresh
    requestOverlay(OVERLAY, { modal: true })
  } finally { checking = false }
}, { immediate: true })

const finish = useSeenWhileOpen(open, () => rows.value.map(r => inboxKey(r.id)))

watch(open, async isOpen => {
  await nextTick()
  const el = dialog.value
  if (!el) return
  if (isOpen && !el.open) el.showModal()
  else if (!isOpen && el.open) el.close()
}, { immediate: true })

function dismiss(go = false) {
  if (!rows.value.length) return
  finish()
  rows.value = []
  if (dialog.value?.open) dialog.value.close()
  releaseOverlay(OVERLAY)
  if (go) void router.push('/team#requests')
}
</script>

<template>
  <dialog v-if="rows.length" ref="dialog" class="pinned-dialog" aria-labelledby="team-requests-title" data-testid="team-requests-popup"
    @cancel.prevent="dismiss()" @click.self="dismiss()">
    <div class="pinned-panel">
      <button type="button" class="pinned-close" :aria-label="pick('Close', '关闭')" @click="dismiss()">×</button>
      <h2 id="team-requests-title" class="pinned-title">{{ requests.length
        ? pick(`${requests.length} join request${requests.length > 1 ? 's' : ''} waiting for you`, `你有 ${requests.length} 条待处理的入队申请`)
        : pick('You have a team invitation', `你收到 ${rows.length} 个队伍邀请`) }}</h2>
      <ul class="text2 mt-3">
        <li v-for="row in rows.slice(0, 5)" :key="row.id">· {{ row.kind === 'request'
          ? pick(`${row.sender_name} wants to join ${row.team_name}`, `${row.sender_name} 申请加入 ${row.team_name}`)
          : pick(`${row.sender_name} invites you to ${row.team_name}`, `${row.sender_name} 邀请你加入 ${row.team_name}`) }}</li>
        <li v-if="rows.length > 5">· …</li>
      </ul>
      <p class="text3 text-sm mt-3">{{ pick('Accept or decline on your team page. Nothing changes until you accept.', '在「队伍」页接受或拒绝；接受前不会改变队伍成员。之后也可以随时点右上角铃铛找到。') }}</p>
      <div class="pinned-actions">
        <button type="button" class="btn sm" data-testid="team-requests-later" @click="dismiss()">{{ pick('Later', '稍后') }}</button>
        <button type="button" class="btn sm primary" data-testid="team-requests-go" @click="dismiss(true)">{{ pick('Review now', '去处理') }} →</button>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
.pinned-dialog { width: min(36rem, calc(100vw - 1.5rem)); max-height: calc(100dvh - 1.5rem); padding: 0; margin: auto;
  border: 1px solid rgba(158,173,255,.35); background: #0b1022; color: #e8ecf8; box-shadow: 0 24px 80px rgba(0,0,0,.6); }
.pinned-dialog::backdrop { background: rgba(2,5,14,.72); backdrop-filter: blur(2px); }
.pinned-panel { position: relative; padding: 1.5rem 1.4rem 1.25rem; overflow-wrap: anywhere; }
@media (min-width: 640px) { .pinned-panel { padding: 2rem 2rem 1.5rem; } }
.pinned-title { padding-right: 2rem; font-size: 1.45rem; font-weight: 600; line-height: 1.3; letter-spacing: -.02em; color: #f5f7ff; }
.pinned-close { position: absolute; top: .6rem; right: .7rem; width: 2.4rem; height: 2.4rem; font-size: 1.6rem; line-height: 1; color: #aeb6c8; background: none; border: 0; cursor: pointer; }
.pinned-close:hover { color: #fff; }
.pinned-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: .75rem; margin-top: 1.5rem; }
</style>
