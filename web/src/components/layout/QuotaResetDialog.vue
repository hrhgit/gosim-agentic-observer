<script setup lang="ts">
// Tells logged-in contestants (with a team) that the organizers reset today's evaluation quota. Shown once per
// reset (lib/popupRules; remembered locally and on the server) and only within 24 hours of the reset. Shares the
// pinned-announcement dialog look; one popup per page load (stores/overlay).
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { useAuth } from '../../stores/auth'
import { loadQuotaResetNotice } from '../../lib/data'
import { QUOTA_RESET_SEEN_KEY, parseQuotaResetSeen, quotaResetText, shouldShowQuotaReset,
  type QuotaResetNotice } from '../../lib/quotaReset'
import { overlayActive, releaseOverlay, requestOverlay } from '../../stores/overlay'
import { quotaResetKey } from '../../lib/popupRules'
import { markSeen, seenKeys, useSeenWhileOpen } from '../../stores/popupSeen'

const OVERLAY = 'quota-reset'
const { t, pick } = useI18n()
const { team } = useAuth()
const dialog = ref<HTMLDialogElement | null>(null)
const notice = ref<QuotaResetNotice | null>(null)
const open = computed(() => !!notice.value && overlayActive(OVERLAY))
const text = computed(() => notice.value ? quotaResetText(notice.value) : null)
let checkedTeam: string | null = null

function readSeen() { try { return localStorage.getItem(QUOTA_RESET_SEEN_KEY) } catch { return null } }

watch(() => team.value?.id, async id => {
  if (!id || id === checkedTeam || notice.value) return
  checkedTeam = id
  try {
    const next = await loadQuotaResetNotice()
    if (!next) return
    if (parseQuotaResetSeen(readSeen()).has(next.id)) markSeen([quotaResetKey(next.id)])
    const seen = await seenKeys()
    if (!shouldShowQuotaReset(next, new Set(seen.has(quotaResetKey(next.id)) ? [next.id] : []))) return
    notice.value = next
    requestOverlay(OVERLAY, { modal: true })
  } catch { /* not shown */ }
}, { immediate: true })

const finish = useSeenWhileOpen(open, () => notice.value ? [quotaResetKey(notice.value.id)] : [])

watch(open, async isOpen => {
  await nextTick()
  const el = dialog.value
  if (!el) return
  if (isOpen && !el.open) el.showModal()
  else if (!isOpen && el.open) el.close()
}, { immediate: true })

function dismiss() {
  if (!notice.value) return
  finish()
  notice.value = null
  if (dialog.value?.open) dialog.value.close()
  releaseOverlay(OVERLAY)
}
</script>

<template>
  <dialog v-if="notice && text" ref="dialog" class="pinned-dialog" aria-labelledby="quota-reset-title" data-testid="quota-reset-popup"
    @cancel.prevent="dismiss" @click.self="dismiss">
    <div class="pinned-panel">
      <button type="button" class="pinned-close" :aria-label="pick('Close', '关闭')" @click="dismiss">×</button>
      <h2 id="quota-reset-title" class="pinned-title">{{ pick('Evaluation counts reset', '评测次数已清零') }}</h2>
      <p class="text2 mt-3" data-testid="quota-reset-text">{{ pick(text.en, text.zh) }}</p>
      <div class="pinned-actions">
        <button type="button" class="btn sm primary" data-testid="quota-reset-ok" @click="dismiss">{{ t('ann.popup_ok') }}</button>
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
