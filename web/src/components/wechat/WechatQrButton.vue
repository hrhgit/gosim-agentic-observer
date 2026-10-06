<script setup lang="ts">
// "WeChat QR" for one participant: the image is fetched as a short-lived signed URL only when opened.
import { nextTick, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { describeError } from '../../lib/errors'
import { useFlash } from '../../stores/flash'
import { reportWechatQr, signedWechatQrUrl } from '../../lib/wechatQrApi'

const props = defineProps<{ userId: string; path: string; name: string; self?: boolean }>()
const { t } = useI18n()
const i18n = useI18n()
const flash = useFlash()
const dialog = ref<HTMLDialogElement | null>(null)
const src = ref('')
const busy = ref(false)

async function open() {
  busy.value = true
  try {
    src.value = await signedWechatQrUrl(props.path)
    await nextTick()
    dialog.value?.showModal()
  } catch (e) { flash.error(describeError(e, i18n, ['wechat_qr.errors'])) }
  finally { busy.value = false }
}
function close() { dialog.value?.close(); src.value = '' }
async function report() {
  const reason = window.prompt(t('wechat_qr.report_prompt'))
  if (reason === null) return
  try { await reportWechatQr(props.userId, reason.trim()); flash.success(t('wechat_qr.reported')); close() }
  catch (e) { flash.error(describeError(e, i18n, ['wechat_qr.errors'])) }
}
</script>

<template>
  <span class="qr-wrap">
  <button type="button" class="copy-btn" data-testid="wechat-qr-open" :disabled="busy" @click="open">{{ t('wechat_qr.show') }}</button>
  <dialog ref="dialog" class="qr-dialog" :aria-label="t('wechat_qr.title')" data-testid="wechat-qr-dialog" @click.self="close" @close="src = ''">
    <div class="qr-panel">
      <button type="button" class="qr-close" :aria-label="t('eggs.mid_autumn.close')" @click="close">×</button>
      <p class="label">{{ t('wechat_qr.title') }}</p>
      <h2 class="qr-name">{{ name }}</h2>
      <img v-if="src" :src="src" :alt="t('wechat_qr.title') + ' · ' + name" class="qr-img" referrerpolicy="no-referrer" draggable="false">
      <p class="help mt-3">{{ t('wechat_qr.view_note') }}</p>
      <button v-if="!self" type="button" class="qr-report" data-testid="wechat-qr-report" @click="report">{{ t('wechat_qr.report') }}</button>
    </div>
  </dialog>
  </span>
</template>

<style scoped>
.qr-wrap { display: inline-block; }
.qr-dialog { width: min(22rem, calc(100vw - 1.5rem)); padding: 0; margin: auto; border: 1px solid rgba(158,173,255,.35);
  background: #0b1022; color: #e8ecf8; box-shadow: 0 24px 80px rgba(0,0,0,.6); }
.qr-dialog::backdrop { background: rgba(2,5,14,.72); backdrop-filter: blur(2px); }
.qr-panel { position: relative; padding: 1.4rem 1.4rem 1.1rem; text-align: center; }
.qr-close { position: absolute; top: .5rem; right: .6rem; width: 2.2rem; height: 2.2rem; font-size: 1.5rem; line-height: 1; color: #aeb6c8; background: none; border: 0; cursor: pointer; }
.qr-name { margin: .2rem 2rem .9rem; font-size: 1.1rem; font-weight: 600; overflow-wrap: anywhere; }
.qr-img { display: block; width: 100%; max-width: 16rem; aspect-ratio: 1; margin: 0 auto; object-fit: contain; background: #fff; border-radius: 6px; }
.qr-report { margin-top: .6rem; border: 0; background: none; color: rgba(205,214,238,.55); font-size: .75rem; text-decoration: underline; cursor: pointer; }
.qr-report:hover { color: #ffb3b3; }
</style>
