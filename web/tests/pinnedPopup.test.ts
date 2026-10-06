import assert from 'node:assert/strict'
import test from 'node:test'
import { parseSeen, pickPinnedPopup, pinnedRows, rememberSeen } from '../src/lib/pinnedPopup.ts'
import { overlayActive, releaseOverlay, requestOverlay } from '../src/stores/overlay.ts'
import { SETTLE_MS } from '../src/lib/popupRules.ts'

const row = (id: string, created_at: string, is_pinned = true, is_published = true) => ({ id, created_at, is_pinned, is_published })
const rows = [
  row('140', '2026-09-28T10:00:00Z'),
  row('147', '2026-09-30T17:00:00Z'),
  row('150', '2026-10-01T09:00:00Z', false),
  row('151', '2026-10-01T10:00:00Z', true, false),
]

test('the newest published pinned announcement pops up, until it is dismissed', () => {
  assert.equal(pickPinnedPopup(rows, new Set())?.id, '147')
  assert.deepEqual(pinnedRows(rows).map(r => r.id), ['147', '140'])
  // Dismissing remembers every pinned id shown then: the older one does not pop up next.
  const stored = rememberSeen(null, pinnedRows(rows).map(r => r.id))
  assert.equal(pickPinnedPopup(rows, parseSeen(stored)), null)
  // A newer pinned announcement pops up again.
  assert.equal(pickPinnedPopup([...rows, row('160', '2026-10-02T08:00:00Z')], parseSeen(stored))?.id, '160')
})

test('the stored list survives bad values and stays bounded', () => {
  assert.deepEqual([...parseSeen('not json')], [])
  assert.deepEqual([...parseSeen('{"a":1}')], [])
  assert.deepEqual([...parseSeen(rememberSeen('[147]', [147, 148]))], ['147', '148'])
  let stored: string | null = null
  for (let i = 0; i < 80; i++) stored = rememberSeen(stored, [i])
  const seen = parseSeen(stored)
  assert.equal(seen.size, 50)
  assert.ok(seen.has('79') && !seen.has('0'))
})

test('one popup per page load: the most important asker wins, the rest wait for a later visit', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  requestOverlay('kimi-plan')
  requestOverlay('pinned-announcement')
  assert.ok(!overlayActive('kimi-plan') && !overlayActive('pinned-announcement'))
  t.mock.timers.tick(SETTLE_MS)
  assert.ok(overlayActive('pinned-announcement') && !overlayActive('kimi-plan'))
  requestOverlay('team-requests')
  assert.ok(!overlayActive('team-requests'))
  releaseOverlay('pinned-announcement')
  assert.ok(!overlayActive('pinned-announcement') && !overlayActive('kimi-plan') && !overlayActive('team-requests'))
})
