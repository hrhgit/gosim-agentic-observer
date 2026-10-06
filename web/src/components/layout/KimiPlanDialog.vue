<script setup lang="ts">
// Tells members of a team that can claim its Kimi Coding Plan code (eligible, codes left, not claimed yet).
// Never shown once the pool is empty (sold out).
// The captain is sent to the dashboard panel; other members are asked to ping the captain. Seen once per team and
// role (lib/popupRules: a change, e.g. becoming captain, shows it again), remembered locally and on the server.
// Shares the pinned-announcement dialog look; one popup per page load (stores/overlay).
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../../composables/useI18n'
import { useAuth } from '../../stores/auth'
import { loadKimiPlanStatus } from '../../lib/data'
import { kimiPlanPopupRole } from '../../lib/kimiPlan'
import { overlayActive, releaseOverlay, requestOverlay } from '../../stores/overlay'
import { kimiPlanKey } from '../../lib/popupRules'
import { markSeen, seenKeys, useSeenWhileOpen } from '../../stores/popupSeen'

const OVERLAY = 'kimi-plan'
const KEY = 'sac.kimi-plan-popup.dismissed.'
const { t } = useI18n()
const route = useRoute()
const { team } = useAuth()
const dialog = ref<HTMLDialogElement | null>(null)
const captain = ref(false)
const teamId = ref<string | null>(null)
const open = computed(() => !!teamId.value && overlayActive(OVERLAY))

function legacyDismissed(id: string) { try { return localStorage.getItem(KEY + id) === '1' } catch { return false } }
const seenKey = ref('')

watch(() => team.value?.id, async id => {
  if (!id || id === teamId.value) return
  try {
    const status = await loadKimiPlanStatus()
    const role = kimiPlanPopupRole(status) // null once the pool is empty: no popup for teams without a code
    if (!role) return
    const key = kimiPlanKey(id, role)
    if (legacyDismissed(id)) markSeen([key])
    if ((await seenKeys()).has(key)) return
    captain.value = role === 'captain'
    seenKey.value = key
    teamId.value = id
    requestOverlay(OVERLAY, { modal: true })
  } catch { /* not shown */ }
}, { immediate: true })
const finish = useSeenWhileOpen(open, () => [seenKey.value])

// Not over the dashboard itself: the panel is right there.
watch([open, () => route.path], async ([isOpen, path]) => {
  await nextTick()
  const el = dialog.value
  if (!el) return
  if (isOpen && path !== '/dashboard' && !el.open) el.showModal()
  else if ((!isOpen || path === '/dashboard') && el.open) el.close()
}, { immediate: true })

function dismiss() {
  if (!teamId.value) return
  finish()
  teamId.value = null
  if (dialog.value?.open) dialog.value.close()
  releaseOverlay(OVERLAY)
}
</script>

<template>
  <dialog v-if="teamId" ref="dialog" class="pinned-dialog" aria-labelledby="kimi-plan-popup-title" data-testid="kimi-plan-popup"
    @cancel.prevent="dismiss" @click.self="dismiss">
    <div class="pinned-panel">
      <button type="button" class="pinned-close" :aria-label="t('ann.popup_close')" @click="dismiss">×</button>
      <span class="label accent">{{ t('kimi_plan.kicker') }}</span>
      <h2 id="kimi-plan-popup-title" class="pinned-title">{{ t('kimi_plan.popup_title') }}</h2>
      <p class="text2 mt-3">{{ captain ? t('kimi_plan.popup_captain') : t('kimi_plan.popup_member') }}</p>
      <div class="pinned-actions">
        <button type="button" class="btn sm" @click="dismiss">{{ t('ann.popup_close') }}</button>
        <router-link v-if="captain" class="btn sm primary" to="/dashboard#kimi-plan" data-testid="kimi-plan-popup-go" @click="dismiss">{{ t('kimi_plan.popup_go') }} →</router-link>
        <button v-else type="button" class="btn sm primary" @click="dismiss">{{ t('ann.popup_ok') }}</button>
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
.pinned-title { margin-top: .75rem; padding-right: 2rem; font-size: 1.45rem; font-weight: 600; line-height: 1.3; letter-spacing: -.02em; color: #f5f7ff; }
.pinned-close { position: absolute; top: .6rem; right: .7rem; width: 2.4rem; height: 2.4rem; font-size: 1.6rem; line-height: 1; color: #aeb6c8; background: none; border: 0; cursor: pointer; }
.pinned-close:hover { color: #fff; }
.pinned-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: .75rem; margin-top: 1.5rem; }
</style>
