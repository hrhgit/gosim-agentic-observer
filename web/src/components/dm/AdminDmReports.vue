<script setup lang="ts">
// Organizers: reported direct messages. Deleting clears the text (both sides see "removed by the organizers");
// dismissing keeps it.
import { onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { describeError } from '../../lib/errors'
import { fmtUtc } from '../../lib/format'
import { useFlash } from '../../stores/flash'
import { adminDeleteDm, adminDismissDmReports, adminDmReports, type DmReportRow } from '../../lib/dmApi'

const { t } = useI18n()
const i18n = useI18n()
const flash = useFlash()
const rows = ref<DmReportRow[]>([])
const busy = ref(false)

async function load() { rows.value = await adminDmReports() }
async function act(action: () => Promise<unknown>) {
  busy.value = true
  try { await action(); flash.success(t('admin.done')); await load() }
  catch (e) { flash.error(describeError(e, i18n, ['dm.errors'])) }
  finally { busy.value = false }
}
const remove = (r: DmReportRow) => { if (window.confirm(t('dm.admin_delete_confirm'))) void act(() => adminDeleteDm(r.message_id)) }
const dismiss = (r: DmReportRow) => act(() => adminDismissDmReports(r.message_id))
onMounted(() => { load().catch(() => { /* older backend or not shown */ }) })
</script>

<template>
  <div v-if="rows.length" class="panel mb-8" data-testid="admin-dm-reports">
    <div class="hd"><h2>{{ t('dm.admin_title') }}</h2><span class="label">{{ rows.length }}</span></div>
    <ul class="dm-reports">
      <li v-for="r in rows" :key="r.message_id">
        <div class="min-w-0">
          <p class="text-sm"><span class="text3">{{ t('dm.admin_from') }}</span> <b>{{ r.sender_name }}</b> <span class="text3">{{ r.sender_email }}</span>
            → <span class="text3">{{ t('dm.admin_to') }}</span> <b>{{ r.recipient_name }}</b> <span class="text3">{{ r.recipient_email }}</span></p>
          <p class="dm-report-body">{{ r.body }}</p>
          <p class="text3 text-sm">{{ fmtUtc(r.sent_at) }} · {{ t('dm.admin_count') }} {{ r.reports }}</p>
          <p v-for="(reason, i) in r.reasons ?? []" :key="i" class="text3 text-sm">“{{ reason }}”</p>
        </div>
        <div class="actions-inline">
          <button type="button" class="btn sm danger" :disabled="busy" @click="remove(r)">{{ t('dm.admin_delete') }}</button>
          <button type="button" class="btn sm" :disabled="busy" @click="dismiss(r)">{{ t('dm.admin_dismiss') }}</button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.dm-reports { display: flex; flex-direction: column; gap: .8rem; margin: 0; padding: 0; list-style: none; }
.dm-reports li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .8rem 1rem; padding-bottom: .8rem; border-bottom: 1px solid rgba(255,255,255,.08); }
.dm-report-body { margin: .4rem 0; padding: .5rem .7rem; border-left: 2px solid #7a2a2a; background: rgba(255,255,255,.04); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
