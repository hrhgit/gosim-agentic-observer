<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { isSafari } from '../../lib/freshness'
import { BROWSER_NOTICE_KEY } from '../../lib/popupRules'
import { overlayActive, releaseOverlay, requestOverlay } from '../../stores/overlay'
import { seenKeys, useSeenWhileOpen } from '../../stores/popupSeen'

// Safari keeps serving stale builds and renders parts of this site differently. The warning covers the whole
// screen, once per person like every popup (lib/popupRules); it is the most important popup of a page load.
const OVERLAY = 'browser-notice'
const { t } = useI18n()
const wanted = ref(false)
const open = computed(() => wanted.value && overlayActive(OVERLAY))
const copied = ref(false)

function lock(on: boolean) { document.documentElement.style.overflow = on ? 'hidden' : '' }

onMounted(async () => {
  if (!isSafari() || (await seenKeys()).has(BROWSER_NOTICE_KEY)) return
  wanted.value = true
  requestOverlay(OVERLAY, { modal: true })
})
watch(open, isOpen => lock(isOpen))
const finish = useSeenWhileOpen(open, () => [BROWSER_NOTICE_KEY])
onUnmounted(() => lock(false))

function dismiss() {
  finish()
  wanted.value = false
  releaseOverlay(OVERLAY)
}
async function copyLink() {
  try {
    await navigator.clipboard.writeText(location.href)
    copied.value = true
    window.setTimeout(() => { copied.value = false }, 2000)
  } catch { /* clipboard blocked; the address bar still works */ }
}
</script>

<template>
  <div v-if="open" class="browser-notice" role="alertdialog" aria-modal="true" :aria-label="t('browser_notice.title')" data-testid="browser-notice">
    <div class="browser-notice-inner">
      <div class="browser-notice-mark" aria-hidden="true">!</div>
      <h2>{{ t('browser_notice.title') }}</h2>
      <p>{{ t('browser_notice.body') }}</p>
      <button type="button" class="browser-notice-copy" @click="copyLink">{{ copied ? t('common.copied') : t('browser_notice.copy') }}</button>
      <button type="button" class="browser-notice-stay" data-testid="browser-notice-dismiss" @click="dismiss">{{ t('browser_notice.stay') }}</button>
    </div>
  </div>
</template>

<style scoped>
.browser-notice {
  position: fixed; inset: 0; z-index: 200;
  display: flex; align-items: center; justify-content: center;
  padding: max(1.5rem, env(safe-area-inset-top)) 1.5rem max(1.5rem, env(safe-area-inset-bottom));
  overflow-y: auto;
  color: #fff;
  background:
    radial-gradient(900px 600px at 50% 0%, rgba(255, 90, 60, .32), transparent 70%),
    rgba(10, 4, 6, .97);
}
.browser-notice-inner { width: 100%; max-width: 44rem; text-align: center; }
.browser-notice-mark {
  display: flex; align-items: center; justify-content: center;
  width: clamp(5rem, 16vw, 8rem); height: clamp(5rem, 16vw, 8rem); margin: 0 auto;
  border: 4px solid #ff5a3c; border-radius: 50%;
  color: #ff5a3c; font-size: clamp(3rem, 10vw, 5rem); font-weight: 800; line-height: 1;
  box-shadow: 0 0 60px rgba(255, 90, 60, .45);
}
.browser-notice h2 {
  margin-top: clamp(1.5rem, 4vw, 2.5rem);
  font-size: clamp(2.4rem, 8vw, 4.8rem); font-weight: 800; line-height: 1.1; letter-spacing: -.03em;
  text-wrap: balance;
}
.browser-notice p {
  margin: clamp(1rem, 3vw, 1.6rem) auto 0; max-width: 34rem;
  font-size: clamp(1.05rem, 2.6vw, 1.35rem); line-height: 1.65; color: rgba(255, 235, 230, .88);
}
.browser-notice-copy {
  display: block; width: 100%; max-width: 26rem; margin: clamp(2rem, 5vw, 2.8rem) auto 0;
  padding: 1.15rem 1.5rem; border: 0; cursor: pointer;
  background: #ff5a3c; color: #fff;
  font-size: clamp(1.1rem, 3vw, 1.35rem); font-weight: 700;
}
.browser-notice-copy:hover { background: #ff7458; }
.browser-notice-stay {
  margin-top: 1.4rem; border: 0; background: none; cursor: pointer;
  color: rgba(255, 255, 255, .5); font-size: .9rem; text-decoration: underline; text-underline-offset: 3px;
}
.browser-notice-stay:hover { color: rgba(255, 255, 255, .8); }
.browser-notice button:focus-visible { outline: 2px solid #fff; outline-offset: 3px; }
</style>
