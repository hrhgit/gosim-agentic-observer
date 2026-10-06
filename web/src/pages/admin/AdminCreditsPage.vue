<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fmtUtc } from '../../lib/format'
import { useAdmin } from '../../composables/useAdmin'
import DashShell from '../../components/layout/DashShell.vue'
import SkeletonRows from '../../components/layout/SkeletonRows.vue'
import StatusPill from '../../components/layout/StatusPill.vue'
import { guessCodeColumn, maskCode, readSheets, type Sheet } from '../../lib/sheetImport'

interface Stat { provider: string; available: number; assigned: number; revoked: number; total: number; note: string }
interface CodeRow { id: number; provider: string; code: string; note: string; status: string; team_id: string | null; team_name: string | null; assigned_by_email: string | null; assigned_at: string | null; created_at: string }
interface TeamRow { id: string; name: string }
interface KimiRow { team_id: string; team_name: string; is_hidden: boolean; captain_name: string | null; captain_email: string | null; qualified: boolean; first_scored_at: string | null; code: string | null; claimed_at: string | null; claimed_by_email: string | null }

const STATUSES = ['available', 'assigned', 'revoked']
const ERROR_NS = ['admin.credits.errors']
const { t, tf, busy, rpc, run, flash, report } = useAdmin()
const stats = ref<Stat[]>([])
const rows = ref<CodeRow[]>([])
const teams = ref<TeamRow[]>([])
const loading = ref(true)
const importForm = ref({ provider: '', note: '', codes: '' })
const assignForm = ref({ team_id: '', provider: '', replace: false })
const filter = ref({ provider: '', status: '' })
const kimiTeams = ref<KimiRow[]>([])
const providerNames = computed(() => stats.value.map(s => s.provider))
const kimiSummary = computed(() => tf('admin.kimi.summary', {
  eligible: kimiTeams.value.filter(r => r.qualified && !r.is_hidden).length,
  claimed: kimiTeams.value.filter(r => r.code).length,
  pool: stats.value.find(s => s.provider === 'kimi')?.available ?? 0,
}))

async function loadStats() {
  stats.value = ((await rpc<any[]>('admin_redeem_stats')) ?? []).map(s => ({ provider: String(s.provider), available: Number(s.available ?? 0), assigned: Number(s.assigned ?? 0), revoked: Number(s.revoked ?? 0), total: Number(s.total ?? 0), note: String(s.note ?? '') }))
}
async function loadCodes() {
  rows.value = ((await rpc<any[]>('admin_redeem_codes', { p_provider: filter.value.provider || null, p_status: filter.value.status || null })) ?? []) as CodeRow[]
}
async function loadTeams() {
  teams.value = ((await rpc<any[]>('admin_teams')) ?? []).map(team => ({ id: String(team.id), name: String(team.name) }))
}
async function loadKimiTeams() {
  kimiTeams.value = ((await rpc<any[]>('admin_kimi_plan_teams')) ?? []) as KimiRow[]
}
async function reload() { await Promise.all([loadStats(), loadCodes(), loadKimiTeams()]) }

async function importCodes() {
  const provider = importForm.value.provider.trim()
  if (!provider || !importForm.value.codes.trim()) return
  let inserted = 0
  const ok = await run(async () => { inserted = Number(await rpc<number>('admin_import_redeem_codes', { p_provider: provider, p_codes: importForm.value.codes, p_note: importForm.value.note.trim() })) })
  if (!ok) return
  importForm.value.codes = ''
  await reload()
  flash.success(tf('admin.credits.imported', { n: inserted }))
}
const xl = ref({ fileName: '', sheets: [] as Sheet[], sheet: 0, hasHeader: true, codeCol: -1, noteCol: -1, provider: 'kimi', note: '', result: '' })
const xlRows = computed(() => xl.value.sheets[xl.value.sheet]?.rows ?? [])
const xlWidth = computed(() => Math.max(0, ...xlRows.value.slice(0, 50).map(r => r.length)))
const xlHeader = computed(() => Array.from({ length: xlWidth.value }, (_, i) => (xl.value.hasHeader ? xlRows.value[0]?.[i]?.trim() : '') || `${t('admin.credits.xl_col')} ${i + 1}`))
const xlBody = computed(() => (xl.value.hasHeader ? xlRows.value.slice(1) : xlRows.value))
const xlPlan = computed(() => {
  const seen = new Set<string>()
  const groups = new Map<string, string[]>()
  let skipped = 0
  if (xl.value.codeCol < 0) return { groups, total: 0, skipped }
  for (const r of xlBody.value) {
    const code = (r[xl.value.codeCol] ?? '').trim()
    if (!code || seen.has(code)) { if (r.some(c => c.trim())) skipped++; continue }
    seen.add(code)
    const note = (xl.value.noteCol >= 0 ? (r[xl.value.noteCol] ?? '').trim() : '') || xl.value.note.trim()
    groups.set(note, [...(groups.get(note) ?? []), code])
  }
  return { groups, total: seen.size, skipped }
})
function xlAutoGuess() {
  const header = xl.value.hasHeader ? (xlRows.value[0] ?? []) : []
  xl.value.codeCol = guessCodeColumn(header)
  if (xl.value.codeCol < 0 && xlWidth.value === 1) xl.value.codeCol = 0
  xl.value.noteCol = header.findIndex(h => /备注|note|remark/i.test(h))
}
const xlDrag = ref(false)
function onXlDrop(e: DragEvent) {
  xlDrag.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file) void loadXlFile(file)
}
async function onXlFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  try { if (file) await loadXlFile(file) } finally { input.value = '' }
}
async function loadXlFile(file: File) {
  xl.value.result = ''
  try {
    xl.value.sheets = await readSheets(file)
    xl.value.fileName = file.name
    xl.value.sheet = Math.max(0, xl.value.sheets.findIndex(s => s.rows.some(r => r.some(c => c.trim()))))
    xlAutoGuess()
  } catch {
    xl.value.sheets = []
    flash.error(t('admin.credits.xl_bad_file'))
  }
}
async function importExcel() {
  const provider = xl.value.provider.trim()
  const plan = xlPlan.value
  if (!provider || !plan.total) return
  let inserted = 0
  const ok = await run(async () => {
    for (const [note, codes] of plan.groups) inserted += Number(await rpc<number>('admin_import_redeem_codes', { p_provider: provider, p_codes: codes.join('\n'), p_note: note }))
  })
  if (!ok) return
  xl.value.result = tf('admin.credits.xl_result', { n: inserted, dup: plan.total - inserted, skipped: plan.skipped })
  xl.value.sheets = []
  await reload()
  flash.success(xl.value.result)
}
async function assignCode() {
  const provider = assignForm.value.provider.trim()
  if (!assignForm.value.team_id || !provider) return
  let code = ''
  const ok = await run(async () => { code = String(await rpc<string>('admin_assign_redeem_code', { p_team_id: assignForm.value.team_id, p_provider: provider, p_replace: assignForm.value.replace })) }, undefined, ERROR_NS)
  if (!ok) return
  flash.success(tf('admin.credits.assigned_flash', { code }))
  await reload()
}
async function revoke(row: CodeRow, remove: boolean) {
  if (!window.confirm(t(remove ? 'admin.credits.delete_confirm' : 'admin.credits.revoke_confirm'))) return
  const ok = await run(() => rpc('admin_revoke_redeem_code', { p_id: row.id, p_delete: remove }), t(remove ? 'admin.credits.deleted_flash' : 'admin.credits.revoked_flash'))
  if (ok) await reload()
}

onMounted(async () => { try { await Promise.all([reload(), loadTeams()]) } catch (e) { report(e) } finally { loading.value = false } })
</script>

<template>
  <DashShell admin :kicker="t('admin.kicker')" :title="t('admin.nav.credits')">
    <div class="panel mt-2">
      <div class="hd"><h2>{{ t('admin.credits.stats') }}</h2><span class="label">{{ t('credits.kicker') }}</span></div>
      <div class="table-wrap">
        <table class="data-table" data-testid="credits-stats">
          <thead><tr><th>{{ t('admin.credits.provider') }}</th><th class="r">{{ t('admin.credits.available') }}</th><th class="r">{{ t('admin.credits.assigned') }}</th><th class="r">{{ t('admin.credits.revoked') }}</th><th class="r">{{ t('admin.credits.total') }}</th><th>{{ t('admin.credits.note') }}</th></tr></thead>
          <tbody>
            <tr v-for="s in stats" :key="s.provider" :data-testid="`credits-stat-${s.provider}`">
              <td class="m">{{ s.provider }}</td>
              <td class="r m" :data-testid="`credits-stat-${s.provider}-available`">{{ s.available }}</td>
              <td class="r m">{{ s.assigned }}</td>
              <td class="r m">{{ s.revoked }}</td>
              <td class="r m">{{ s.total }}</td>
              <td class="text3 text-sm">{{ s.note || '—' }}</td>
            </tr>
            <tr v-if="loading"><td colspan="6" class="p-0"><SkeletonRows :rows="3" :cols="5" :label="t('common.loading')" /></td></tr>
            <tr v-else-if="!stats.length"><td colspan="6" class="text3">{{ t('common.no_data') }}</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="mt-8 grid gap-8 lg:grid-cols-2">
      <form class="panel" @submit.prevent="importCodes">
        <div class="hd"><h2>{{ t('admin.credits.import') }}</h2></div>
        <div class="grid-form">
          <label class="field"><span>{{ t('admin.credits.import_provider') }}</span><input data-testid="credits-import-provider" v-model="importForm.provider" type="text" required list="credits-provider-names" autocomplete="off"></label>
          <label class="field"><span>{{ t('admin.credits.import_note') }}</span><input data-testid="credits-import-note" v-model="importForm.note" type="text"></label>
          <label class="field full"><span>{{ t('admin.credits.import_codes') }}</span><textarea data-testid="credits-import-codes" v-model="importForm.codes" rows="6" required spellcheck="false"></textarea></label>
        </div>
        <button data-testid="credits-import-submit" class="btn primary sm" type="submit" :disabled="busy">{{ t('admin.credits.import_submit') }}</button>
      </form>

      <form class="panel lg:col-span-2" data-testid="credits-excel" @submit.prevent="importExcel">
        <div class="hd"><h2>{{ t('admin.credits.xl_title') }}</h2><span class="label">{{ xl.fileName }}</span></div>
        <p class="text2 text-sm mb-4">{{ t('admin.credits.xl_lede') }}</p>
        <div class="grid-form">
          <div class="field full"><span>{{ t('admin.credits.xl_file') }}</span>
            <label class="xl-drop" :class="{ on: xlDrag }" @dragover.prevent="xlDrag = true" @dragleave="xlDrag = false" @drop.prevent="onXlDrop">
              <input data-testid="credits-excel-file" type="file" accept=".xlsx,.xls,.csv,.tsv" class="xl-file" @change="onXlFile">
              <span>{{ xl.fileName || t('admin.credits.xl_drop') }}</span>
            </label>
          </div>
          <template v-if="xl.sheets.length">
            <label v-if="xl.sheets.length > 1" class="field"><span>{{ t('admin.credits.xl_sheet') }}</span><select v-model.number="xl.sheet" class="input" @change="xlAutoGuess"><option v-for="(s, i) in xl.sheets" :key="i" :value="i">{{ s.name }}</option></select></label>
            <label class="field"><span>{{ t('admin.credits.xl_code_col') }}</span><select v-model.number="xl.codeCol" class="input" data-testid="credits-excel-code-col" required><option :value="-1" disabled>—</option><option v-for="(h, i) in xlHeader" :key="i" :value="i">{{ h }}</option></select></label>
            <label class="field"><span>{{ t('admin.credits.xl_note_col') }}</span><select v-model.number="xl.noteCol" class="input"><option :value="-1">{{ t('admin.credits.xl_none') }}</option><option v-for="(h, i) in xlHeader" :key="i" :value="i">{{ h }}</option></select></label>
            <label class="field"><span>{{ t('admin.credits.import_provider') }}</span><input v-model="xl.provider" type="text" required list="credits-provider-names" autocomplete="off"></label>
            <label class="field"><span>{{ t('admin.credits.xl_default_note') }}</span><input v-model="xl.note" type="text"></label>
          </template>
        </div>
        <template v-if="xl.sheets.length">
          <label class="check"><input v-model="xl.hasHeader" type="checkbox" @change="xlAutoGuess"> {{ t('admin.credits.xl_has_header') }}</label>
          <div class="table-wrap my-4">
            <table class="tbl">
              <thead><tr><th v-for="(h, i) in xlHeader" :key="i">{{ h }}<template v-if="i === xl.codeCol"> · {{ t('admin.credits.code') }}</template></th></tr></thead>
              <tbody>
                <tr v-for="(r, ri) in xlBody.slice(0, 3)" :key="ri"><td v-for="(_, i) in xlHeader" :key="i" class="mono">{{ i === xl.codeCol ? maskCode(r[i] ?? '') : (r[i] ?? '') }}</td></tr>
                <tr v-if="!xlBody.length"><td :colspan="xlHeader.length || 1" class="text3">{{ t('common.no_data') }}</td></tr>
              </tbody>
            </table>
          </div>
          <p class="text2 text-sm mb-4">{{ tf('admin.credits.xl_summary', { n: xlPlan.total, skipped: xlPlan.skipped }) }}</p>
          <button data-testid="credits-excel-submit" class="btn primary sm" type="submit" :disabled="busy || !xlPlan.total">{{ tf('admin.credits.xl_submit', { n: xlPlan.total }) }}</button>
        </template>
        <p v-if="xl.result" class="text2 text-sm mt-4" data-testid="credits-excel-result">{{ xl.result }}</p>
      </form>

      <form class="panel" @submit.prevent="assignCode">
        <div class="hd"><h2>{{ t('admin.credits.assign') }}</h2></div>
        <div class="grid-form">
          <label class="field"><span>{{ t('admin.credits.assign_team') }}</span>
            <select data-testid="credits-assign-team" v-model="assignForm.team_id" required>
              <option value="" disabled>—</option>
              <option v-for="team in teams" :key="team.id" :value="team.id">{{ team.name }}</option>
            </select>
          </label>
          <label class="field"><span>{{ t('admin.credits.assign_provider') }}</span><input data-testid="credits-assign-provider" v-model="assignForm.provider" type="text" required list="credits-provider-names" autocomplete="off"></label>
        </div>
        <label class="check"><input data-testid="credits-assign-replace" v-model="assignForm.replace" type="checkbox"> {{ t('admin.credits.replace') }}</label>
        <button data-testid="credits-assign-submit" class="btn sm" type="submit" :disabled="busy">{{ t('admin.credits.assign_submit') }}</button>
      </form>
    </div>
    <datalist id="credits-provider-names"><option v-for="name in providerNames" :key="name" :value="name"></option></datalist>

    <div class="panel mt-8" data-testid="kimi-plan-admin">
      <div class="hd"><h2>{{ t('admin.kimi.title') }}</h2><span class="label">{{ kimiSummary }}</span></div>
      <p class="text2 text-sm mb-4">{{ t('admin.kimi.lede') }}</p>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>{{ t('admin.kimi.team') }}</th><th>{{ t('admin.kimi.captain') }}</th><th>{{ t('admin.kimi.first_scored') }}</th><th>{{ t('admin.kimi.code') }}</th><th>{{ t('admin.kimi.claimed_by') }}</th><th>{{ t('admin.kimi.claimed_at') }}</th></tr></thead>
          <tbody>
            <tr v-for="row in kimiTeams" :key="row.team_id" :data-testid="`kimi-plan-team-${row.team_id}`">
              <td>{{ row.team_name }}<template v-if="row.is_hidden"> · <span class="label">{{ t('admin.kimi.hidden') }}</span></template></td>
              <td class="xs">{{ row.captain_name ?? '—' }}<br><span class="text3">{{ row.captain_email ?? '' }}</span></td>
              <td class="m xs">{{ fmtUtc(row.first_scored_at, { short: true }) }}</td>
              <td class="m">{{ row.code ?? '—' }}</td>
              <td class="xs">{{ row.claimed_by_email ?? '—' }}</td>
              <td class="m xs">{{ fmtUtc(row.claimed_at, { short: true }) }}</td>
            </tr>
            <tr v-if="loading"><td colspan="6" class="p-0"><SkeletonRows :rows="3" :cols="5" :label="t('common.loading')" /></td></tr>
            <tr v-else-if="!kimiTeams.length"><td colspan="6" class="text3">{{ t('common.no_data') }}</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="panel mt-8">
      <div class="hd"><h2>{{ t('admin.credits.list') }}</h2></div>
      <form class="actions-inline mb-4" @submit.prevent="loadCodes">
        <select v-model="filter.provider" class="input w-auto" data-testid="credits-filter-provider"><option value="">{{ t('admin.credits.any_provider') }}</option><option v-for="name in providerNames" :key="name" :value="name">{{ name }}</option></select>
        <select v-model="filter.status" class="input w-auto" data-testid="credits-filter-status"><option value="">{{ t('admin.credits.any_status') }}</option><option v-for="s in STATUSES" :key="s" :value="s">{{ t(`admin.credits.status.${s}`) }}</option></select>
        <button class="btn sm" type="submit" :disabled="busy">{{ t('common.filter') }}</button>
      </form>
      <div class="table-wrap">
        <table class="data-table" data-testid="credits-list">
          <thead><tr><th>{{ t('admin.credits.code') }}</th><th>{{ t('admin.credits.provider') }}</th><th>{{ t('common.status') }}</th><th>{{ t('admin.credits.team') }}</th><th>{{ t('admin.credits.assigned_by') }}</th><th>{{ t('admin.credits.assigned_at') }}</th><th>{{ t('admin.credits.note') }}</th><th>{{ t('common.actions') }}</th></tr></thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id" :data-testid="`credits-code-row-${row.id}`">
              <td class="m">{{ row.code }}</td>
              <td class="m xs">{{ row.provider }}</td>
              <td><StatusPill :status="row.status" ns="admin.credits.status" /></td>
              <td>{{ row.team_name ?? '—' }}</td>
              <td class="xs">{{ row.assigned_by_email ?? '—' }}</td>
              <td class="m xs">{{ fmtUtc(row.assigned_at, { short: true }) }}</td>
              <td class="text3 text-xs">{{ row.note }}</td>
              <td>
                <div class="actions-inline">
                  <button v-if="row.status !== 'revoked'" type="button" class="copy-btn" :disabled="busy" @click="revoke(row, false)">{{ t('admin.credits.revoke') }}</button>
                  <button v-if="row.status === 'available'" type="button" class="copy-btn danger" :disabled="busy" @click="revoke(row, true)">{{ t('admin.credits.delete') }}</button>
                </div>
              </td>
            </tr>
            <tr v-if="loading"><td colspan="8" class="p-0"><SkeletonRows :rows="5" :cols="5" :label="t('common.loading')" /></td></tr>
            <tr v-else-if="!rows.length"><td colspan="8" class="text3">{{ t('common.no_data') }}</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </DashShell>
</template>

<style scoped>
.xl-drop { position: relative; display: flex; align-items: center; justify-content: center; min-height: 5rem; padding: 1rem; border: 1px dashed rgba(255,255,255,.35); border-radius: .5rem; cursor: pointer; color: #bdbdbd; text-align: center; }
.xl-drop.on { border-color: #fff; background: rgba(255,255,255,.06); }
.xl-file { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer; }
</style>
