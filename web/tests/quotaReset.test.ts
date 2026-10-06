import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { normalizeQuotaResetNotice, parseQuotaResetSeen, quotaResetText, rememberQuotaResetSeen, shouldShowQuotaReset } from '../src/lib/quotaReset.ts'

const now = Date.parse('2026-10-04T12:00:00Z')
const raw = { id: 'r1', reset_at: '2026-10-04T10:00:00Z', phases: [
  { phase_id: 'a', name_en: 'Practice', name_zh: '练习', daily_batches: 40 },
  { phase_id: 'b', name_en: 'Online', name_zh: '线上赛', daily_batches: 40 }] }

test('the backend payload is normalized defensively', () => {
  assert.equal(normalizeQuotaResetNotice(null), null)
  assert.equal(normalizeQuotaResetNotice({ id: 'x', reset_at: '2026-10-04T10:00:00Z', phases: [] }), null)
  assert.equal(normalizeQuotaResetNotice({ id: '', reset_at: 'x', phases: raw.phases }), null)
  assert.equal(normalizeQuotaResetNotice({ ...raw, phases: [{ daily_batches: '20' }] })?.phases[0].daily_batches, 20)
})

test('shown once per reset, only within 24 hours', () => {
  const n = normalizeQuotaResetNotice(raw)
  assert.equal(shouldShowQuotaReset(n, new Set(), now), true)
  assert.equal(shouldShowQuotaReset(n, parseQuotaResetSeen(rememberQuotaResetSeen(null, 'r1')), now), false)
  assert.equal(shouldShowQuotaReset(n, parseQuotaResetSeen(rememberQuotaResetSeen(null, 'r0')), now), true)
  assert.equal(shouldShowQuotaReset(n, new Set(), Date.parse('2026-10-05T10:00:01Z')), false)
  assert.equal(shouldShowQuotaReset(null, new Set(), now), false)
})

test('unreadable or blocked storage never throws and means not dismissed', () => {
  assert.deepEqual([...parseQuotaResetSeen(null)], [])
  assert.deepEqual([...parseQuotaResetSeen('{oops')], [])
  assert.deepEqual([...parseQuotaResetSeen('"r1"')], [])
  const many = Array.from({ length: 30 }, (_, i) => 'r' + i).reduce<string | null>((acc, id) => rememberQuotaResetSeen(acc, id), null)
  const seen = parseQuotaResetSeen(many)
  assert.equal(seen.size, 20)
  assert.ok(seen.has('r29') && !seen.has('r0'))
})

test('the text uses the backend daily amount, not a fixed 40', () => {
  const n = normalizeQuotaResetNotice(raw)!
  assert.equal(quotaResetText(n).zh, '今天每队重新有 40 次评测机会（按北京时间 8 点 / UTC 0 点的每日周期计算）。之前的评测记录和成绩不受影响。')
  assert.match(quotaResetText(n).en, /Every team has 40 evaluations again today/)
  const other = normalizeQuotaResetNotice({ ...raw, phases: [{ ...raw.phases[0], daily_batches: 25 }] })!
  assert.match(quotaResetText(other).zh, /今天每队重新有 25 次评测机会/)
  const mixed = normalizeQuotaResetNotice({ ...raw, phases: [raw.phases[0], { ...raw.phases[1], daily_batches: 10 }] })!
  assert.match(quotaResetText(mixed).zh, /练习 40 次、线上赛 10 次/)
})

test('the popup takes turns with the other popups and is mounted app-wide', () => {
  const dialog = readFileSync(new URL('../src/components/layout/QuotaResetDialog.vue', import.meta.url), 'utf8')
  assert.match(dialog, /requestOverlay\(OVERLAY, \{ modal: true \}\)/)
  assert.match(dialog, /overlayActive\(OVERLAY\)/)
  assert.match(dialog, /releaseOverlay\(OVERLAY\)/)
  const app = readFileSync(new URL('../src/App.vue', import.meta.url), 'utf8')
  assert.match(app, /<QuotaResetDialog \/>/)
})
