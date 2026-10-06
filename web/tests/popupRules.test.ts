import assert from 'node:assert/strict'
import test from 'node:test'
import { announcementOffKey, announcementSnoozeKey, announcementState, beijingDay, contentHash, needsLegacySnooze, pickAnnouncement, kimiPlanKey, parseSeenList, pickPopup, rememberSeenList } from '../src/lib/popupRules.ts'

test('one popup per page load: the most important candidate wins, whatever the asking order', () => {
  assert.equal(pickPopup([]), null)
  assert.equal(pickPopup(['kimi-plan', 'pinned-announcement']), 'pinned-announcement')
  assert.equal(pickPopup(['pinned-announcement', 'quota-reset', 'team-requests']), 'team-requests')
  assert.equal(pickPopup(['kimi-plan', 'browser-notice']), 'browser-notice')
  assert.equal(pickPopup(['something-new', 'kimi-plan']), 'kimi-plan')
})

test('an announcement comes back daily until switched off; only notify_version re-arms a switched-off one', () => {
  const a = { id: 7, title_en: 'Hi', body_zh: 'y', created_at: '2026-10-04', is_pinned: true }
  const day = '2026-10-05'
  assert.equal(announcementState(a, new Set(), day), 'show')
  assert.equal(announcementState(a, new Set([announcementSnoozeKey(a, day)]), day), 'snoozed')
  assert.equal(announcementState(a, new Set([announcementSnoozeKey(a, '2026-10-04')]), day), 'show')
  assert.equal(announcementState(a, new Set([announcementOffKey(a)]), day), 'off')
  // Text edits keep the keys; "remind everyone" (notify_version + 1) re-arms a switched-off announcement.
  assert.equal(announcementOffKey({ ...a, body_zh: 'edited' }), 'ann-off:7:v1')
  assert.equal(announcementState({ ...a, notify_version: 2 }, new Set([announcementOffKey(a)]), day), 'show')
})

test('a Beijing day starts at 16:00 UTC', () => {
  assert.equal(beijingDay(Date.parse('2026-10-04T15:59:00Z')), '2026-10-04')
  assert.equal(beijingDay(Date.parse('2026-10-04T16:00:00Z')), '2026-10-05')
})

test('closed before snoozing existed = snoozed once (today), never switched off', () => {
  const a = { id: 7 }
  assert.ok(needsLegacySnooze(a, new Set(['ann:7:v1'])))
  assert.ok(needsLegacySnooze(a, new Set(['ann:7:1a2b3c4d'])))
  assert.ok(needsLegacySnooze(a, new Set(), new Set(['7'])))
  assert.ok(!needsLegacySnooze(a, new Set(['ann:7:v1', 'ann-snooze:7:v1:2026-10-04'])))
  assert.ok(!needsLegacySnooze(a, new Set(['ann:7:v1', 'ann-off:7:v1'])))
  assert.ok(!needsLegacySnooze(a, new Set(['ann:17:1a2b3c4d', 'ann:70:v1'])))
  assert.ok(!needsLegacySnooze({ id: 7, notify_version: 2 }, new Set(['ann:7:1a2b3c4d']), new Set(['7'])))
})

test('one announcement per page load, rotating through the ones not yet shown today', () => {
  const day = '2026-10-05'
  const rows = [{ id: 3 }, { id: 2 }, { id: 1 }]
  const seen = new Set<string>([announcementOffKey(rows[1])])
  assert.equal(pickAnnouncement(rows, seen, day)?.id, 3)
  seen.add(announcementSnoozeKey(rows[0], day))
  assert.equal(pickAnnouncement(rows, seen, day)?.id, 1)
  seen.add(announcementSnoozeKey(rows[2], day))
  assert.equal(pickAnnouncement(rows, seen, day), null)
  assert.equal(pickAnnouncement(rows, seen, '2026-10-06')?.id, 3)
  assert.notEqual(contentHash('ab', 'c'), contentHash('a', 'bc'))
  assert.notEqual(kimiPlanKey('t', 'member'), kimiPlanKey('t', 'captain'))
})

test('the seen list survives junk, keeps the newest and has no duplicates', () => {
  assert.deepEqual(parseSeenList('not json'), [])
  assert.deepEqual(parseSeenList(null), [])
  assert.deepEqual(parseSeenList('{"a":1}'), [])
  assert.deepEqual(rememberSeenList(['a', 'b'], ['a', 'c', 'c']), ['b', 'a', 'c'])
  const big = Array.from({ length: 400 }, (_, i) => `k${i}`)
  const kept = rememberSeenList(big, ['new'])
  assert.equal(kept.length, 300)
  assert.equal(kept.at(-1), 'new')
})
