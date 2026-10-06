<script setup lang="ts">
import { useI18n } from '../composables/useI18n'
defineProps<{ compact?: boolean }>()
const { pick } = useI18n()
const qr = `${import.meta.env.BASE_URL}media/wechat-group-qr.png`
</script>

<template>
  <div class="wechat-group" :class="{ compact }" data-testid="wechat-group">
    <img :src="qr" :alt="pick('WeChat group QR code', '微信群二维码')" width="528" height="528" loading="lazy" />
    <div>
      <p class="wg-title">{{ pick('Join the WeChat group', '加入微信群') }}</p>
      <p class="wg-name">{{ pick('GOSIM 2026 Agentic Observer Hackathon group', 'GOSIM 2026 智能体巡天黑客松群') }}</p>
      <p v-if="!compact" class="wg-hint">{{ pick('Scan with WeChat to find teammates, get announcements and ask questions.', '用微信扫码入群：找队友、收通知、问问题。') }}</p>
      <p class="wg-hint">{{ pick('QR code valid until Oct 8', '二维码 10 月 8 日前有效') }}</p>
    </div>
  </div>
</template>

<style scoped>
.wechat-group { display: flex; flex-wrap: wrap; align-items: center; gap: 1.25rem; }
.wechat-group img { width: 168px; height: 168px; background: #fff; border-radius: 10px; padding: 6px; }
.wechat-group.compact img { width: 112px; height: 112px; }
.wg-title { font-weight: 600; font-size: 1.05rem; }
.wg-name { margin-top: .25rem; }
.wg-hint { margin-top: .35rem; font-size: .8rem; opacity: .7; }
</style>
