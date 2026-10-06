<script setup lang="ts">
// 找队友: signed-in people without a WeChat QR code get a one-line prompt that uploads one right here
// (same checks as the profile panel; visibility starts at friends-only and is changed on the profile page).
import { onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { describeError } from '../../lib/errors'
import { useAuth } from '../../stores/auth'
import { useFlash } from '../../stores/flash'
import { qrFileProblem } from '../../lib/wechatQr'
import { loadMyWechatQr, uploadWechatQr } from '../../lib/wechatQrApi'

const { t } = useI18n()
const i18n = useI18n()
const flash = useFlash()
const { isLoggedIn } = useAuth()
const state = ref<'hidden' | 'ask' | 'done'>('hidden')
const busy = ref(false)
const input = ref<HTMLInputElement | null>(null)

onMounted(async () => {
  if (!isLoggedIn.value) return
  try { if (!(await loadMyWechatQr())) state.value = 'ask' } catch { /* not shown */ }
})
async function onFile(event: Event) {
  const el = event.target as HTMLInputElement
  const file = el.files?.[0]
  el.value = ''
  if (!file) return
  const problem = qrFileProblem(file)
  if (problem) { flash.error(t(`wechat_qr.errors.${problem}`)); return }
  busy.value = true
  try { await uploadWechatQr(file); state.value = 'done'; flash.success(t('wechat_qr.uploaded')) }
  catch (e) { flash.error(describeError(e, i18n, ['wechat_qr.errors'])) }
  finally { busy.value = false }
}
</script>

<template>
  <div v-if="isLoggedIn && state !== 'hidden'" class="qr-prompt" data-testid="wechat-qr-prompt">
    <template v-if="state === 'ask'">
      <span class="qr-prompt-icon" aria-hidden="true"></span>
      <span class="qr-prompt-text">{{ t('wechat_qr.prompt') }}</span>
      <button type="button" class="btn sm primary" data-testid="wechat-qr-prompt-upload" :disabled="busy" @click="input?.click()">{{ busy ? t('common.working') : t('wechat_qr.prompt_button') }}</button>
      <input ref="input" type="file" accept="image/png,image/jpeg,image/webp" class="sr-only" data-testid="wechat-qr-prompt-file" @change="onFile">
    </template>
    <template v-else>
      <span class="qr-prompt-text">{{ t('wechat_qr.prompt_done') }}</span>
      <router-link to="/profile#wechat-qr" class="accent-l underline underline-offset-2 text-sm">{{ t('wechat_qr.prompt_settings') }} →</router-link>
    </template>
  </div>
</template>

<style scoped>
.qr-prompt { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem 1rem; padding: .85rem 1rem;
  border: 1px solid rgba(7,193,96,.4); background: linear-gradient(90deg, rgba(7,193,96,.12), rgba(7,193,96,.03)); }
.qr-prompt-icon { width: 1.1rem; height: 1.1rem; flex: none; border-radius: 3px;
  background: conic-gradient(#07c160 0 25%, transparent 0 50%, #07c160 0 75%, transparent 0) 0 0 / 50% 50%; outline: 2px solid #07c160; outline-offset: 1px; }
.qr-prompt-text { flex: 1 1 14rem; min-width: 0; color: #e8ecf8; }
</style>
