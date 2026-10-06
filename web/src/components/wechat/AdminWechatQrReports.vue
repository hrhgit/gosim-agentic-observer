<script setup lang="ts">
// Organizers: reported WeChat QR codes. Removing deletes the stored image; dismissing keeps it.
import { onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { describeError } from '../../lib/errors'
import { useFlash } from '../../stores/flash'
import { adminDeleteWechatQr, adminDismissWechatQrReports, adminWechatQrReports, signedWechatQrUrl } from '../../lib/wechatQrApi'

type Row = Awaited<ReturnType<typeof adminWechatQrReports>>[number] & { src?: string }
const { t } = useI18n()
const i18n = useI18n()
const flash = useFlash()
const rows = ref<Row[]>([])
const busy = ref(false)

async function load() {
  const list: Row[] = await adminWechatQrReports()
  await Promise.all(list.map(async r => { if (r.current) r.src = await signedWechatQrUrl(r.path).catch(() => '') }))
  rows.value = list
}
async function act(action: () => Promise<unknown>) {
  busy.value = true
  try { await action(); flash.success(t('admin.done')); await load() }
  catch (e) { flash.error(describeError(e, i18n, ['wechat_qr.errors'])) }
  finally { busy.value = false }
}
const remove = (r: Row) => { if (window.confirm(t('wechat_qr.admin_remove_confirm'))) void act(() => adminDeleteWechatQr(r.owner_id)) }
const dismiss = (r: Row) => act(() => adminDismissWechatQrReports(r.owner_id))
onMounted(() => { load().catch(() => { /* not shown */ }) })
</script>

<template>
  <div v-if="rows.length" class="panel mb-8" data-testid="admin-wechat-qr-reports">
    <div class="hd"><h2>{{ t('wechat_qr.admin_title') }}</h2><span class="label">{{ rows.length }}</span></div>
    <ul class="qr-reports">
      <li v-for="r in rows" :key="r.owner_id + r.path">
        <img v-if="r.src" :src="r.src" alt="" class="qr-thumb" referrerpolicy="no-referrer">
        <div class="min-w-0">
          <b>{{ r.owner_name }}</b> <span class="text3 text-sm">{{ r.owner_email }}</span>
          <p class="text-sm">{{ t('wechat_qr.admin_count') }} {{ r.reports }}<template v-if="!r.current"> · {{ t('wechat_qr.admin_replaced') }}</template></p>
          <p v-for="(reason, i) in r.reasons ?? []" :key="i" class="text3 text-sm">“{{ reason }}”</p>
        </div>
        <div class="actions-inline">
          <button v-if="r.current" type="button" class="btn sm danger" :disabled="busy" @click="remove(r)">{{ t('wechat_qr.admin_remove') }}</button>
          <button type="button" class="btn sm" :disabled="busy" @click="dismiss(r)">{{ t('wechat_qr.admin_dismiss') }}</button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.qr-reports { display: flex; flex-direction: column; gap: .8rem; margin: 0; padding: 0; list-style: none; }
.qr-reports li { display: flex; flex-wrap: wrap; gap: .8rem 1rem; align-items: flex-start; padding-bottom: .8rem; border-bottom: 1px solid rgba(255,255,255,.08); }
.qr-thumb { width: 6rem; height: 6rem; object-fit: contain; background: #fff; border-radius: 4px; }
</style>
