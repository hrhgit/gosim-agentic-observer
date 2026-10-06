<script setup lang="ts">
// Profile: upload, replace or delete my WeChat QR code and choose who may see it (friends by default).
import { nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../../composables/useI18n'
import { describeError } from '../../lib/errors'
import { useFlash } from '../../stores/flash'
import { qrFileProblem, type MyQr, type QrVisibility } from '../../lib/wechatQr'
import { deleteMyWechatQr, loadMyWechatQr, setWechatQrVisibility, signedWechatQrUrl, uploadWechatQr } from '../../lib/wechatQrApi'

const { t } = useI18n()
const route = useRoute()
const i18n = useI18n()
const flash = useFlash()
const qr = ref<MyQr | null>(null)
const preview = ref('')
const busy = ref(false)
const input = ref<HTMLInputElement | null>(null)
const errorText = (e: unknown) => describeError(e, i18n, ['wechat_qr.errors'])

async function reload() {
  qr.value = await loadMyWechatQr()
  preview.value = qr.value ? await signedWechatQrUrl(qr.value.path).catch(() => '') : ''
}
async function run(action: () => Promise<unknown>, success: string) {
  busy.value = true
  try { await action(); await reload(); flash.success(success) }
  catch (e) { flash.error(errorText(e)) }
  finally { busy.value = false }
}
function onFile(event: Event) {
  const el = event.target as HTMLInputElement
  const file = el.files?.[0]
  el.value = ''
  if (!file) return
  const problem = qrFileProblem(file)
  if (problem) { flash.error(t(`wechat_qr.errors.${problem}`)); return }
  void run(() => uploadWechatQr(file), t('wechat_qr.uploaded'))
}
function remove() { if (window.confirm(t('wechat_qr.delete_confirm'))) void run(() => deleteMyWechatQr(), t('wechat_qr.deleted')) }
function setVisibility(v: QrVisibility) {
  if (!qr.value || qr.value.visibility === v) return
  void run(() => setWechatQrVisibility(v), t('wechat_qr.visibility_saved'))
}
onMounted(async () => {
  await reload().catch(() => { /* panel stays empty */ })
  // Opened from the menu or the 找队友 prompt: bring the panel into view and put focus on the upload button.
  if (route.hash === '#wechat-qr') {
    await nextTick()
    document.getElementById('wechat-qr')?.scrollIntoView({ block: 'start' }); window.scrollBy(0, -80)
    ;(document.querySelector('[data-testid=wechat-qr-upload]') as HTMLButtonElement | null)?.focus({ preventScroll: true })
  }
})
</script>

<template>
  <div id="wechat-qr" class="panel" data-testid="wechat-qr-panel">
    <div class="hd"><h2>{{ t('wechat_qr.title') }}</h2><span class="text3 text-sm">{{ t('common.optional') }}</span></div>
    <p class="text2 text-sm">{{ t('wechat_qr.lede') }}</p>
    <div class="qr-own mt-4">
      <img v-if="preview" :src="preview" :alt="t('wechat_qr.title')" class="qr-thumb" referrerpolicy="no-referrer" data-testid="wechat-qr-preview">
      <div class="qr-own-controls">
        <div class="actions-inline">
          <button type="button" class="btn sm" data-testid="wechat-qr-upload" :disabled="busy" @click="input?.click()">{{ busy ? t('common.working') : (qr ? t('wechat_qr.replace') : t('wechat_qr.upload')) }}</button>
          <button v-if="qr" type="button" class="btn sm" data-testid="wechat-qr-delete" :disabled="busy" @click="remove">{{ t('wechat_qr.delete') }}</button>
        </div>
        <fieldset v-if="qr" class="qr-vis mt-3" :disabled="busy">
          <legend class="label">{{ t('wechat_qr.visibility') }}</legend>
          <label class="check"><input type="radio" name="qr-vis" value="friends" :checked="qr.visibility === 'friends'" data-testid="wechat-qr-vis-friends" @change="setVisibility('friends')"> {{ t('wechat_qr.vis_friends') }}</label>
          <label class="check"><input type="radio" name="qr-vis" value="all" :checked="qr.visibility === 'all'" data-testid="wechat-qr-vis-all" @change="setVisibility('all')"> {{ t('wechat_qr.vis_all') }}</label>
        </fieldset>
        <small class="help">{{ t('wechat_qr.hint') }}</small>
      </div>
    </div>
    <input ref="input" type="file" accept="image/png,image/jpeg,image/webp" class="sr-only" data-testid="wechat-qr-file" @change="onFile">
  </div>
</template>

<style scoped>
.qr-own { display: flex; flex-wrap: wrap; gap: 1rem 1.25rem; align-items: flex-start; }
.qr-thumb { width: 7.5rem; height: 7.5rem; object-fit: contain; background: #fff; border-radius: 6px; flex: none; }
.qr-own-controls { display: flex; flex-direction: column; gap: .35rem; min-width: 0; flex: 1 1 12rem; }
.qr-vis { border: 0; padding: 0; margin: 0; }
.qr-vis .check { margin: .25rem 0; }
</style>
