import assert from 'node:assert/strict'
import test from 'node:test'
import { DM_MAX_LENGTH, FAB_HIDE, checkBody, fabBadge, mergeMessages, messageSegments, normalizeOverview, normalizeThread, sendOutcome, shortTime } from '../src/lib/dm.ts'
import zh from '../src/i18n/zh.json' with { type: 'json' }
import en from '../src/i18n/en.json' with { type: 'json' }

test('the badge adds friend requests and unread messages, capped at 99+', () => {
  assert.equal(fabBadge(0, 0), '')
  assert.equal(fabBadge(2, 3), '5')
  assert.equal(fabBadge(-1, 0), '')
  assert.equal(fabBadge(60, 60), '99+')
})

test('a message is trimmed, must not be empty and is at most 1000 characters', () => {
  assert.deepEqual(checkBody('  hi \n'), { body: 'hi' })
  assert.deepEqual(checkBody(' \n\t '), { error: 'empty' })
  assert.deepEqual(checkBody('字'.repeat(DM_MAX_LENGTH)), { body: '字'.repeat(DM_MAX_LENGTH) })
  assert.deepEqual(checkBody('x'.repeat(DM_MAX_LENGTH + 1)), { error: 'too_long' })
  // Characters, not UTF-16 units: 1000 emoji fit.
  assert.ok('body' in checkBody('😀'.repeat(DM_MAX_LENGTH)))
})

test('send answers become an id or a thrown code', () => {
  assert.equal(sendOutcome({ id: 7, created_at: 'x' }), 7)
  assert.throws(() => sendOutcome({ error: 'rate_minute' }), /rate_minute/)
  assert.throws(() => sendOutcome(null), /generic/)
})

test('only http(s) links become links; no images, no markup', () => {
  assert.deepEqual(messageSegments('see https://a.example/x.png, and <b>javascript:alert(1)</b>'), [
    { kind: 'text', text: 'see ' }, { kind: 'link', href: 'https://a.example/x.png' }, { kind: 'text', text: ', and <b>javascript:alert(1)</b>' }])
  assert.deepEqual(messageSegments(null), [])
})

test('answers are normalized defensively', () => {
  const o = normalizeOverview({ requests: 2, incoming: [{ id: 'r' }], people: [{ user_id: 'u', name: 'A', unread: '3', is_friend: true, can_send: true, last_body: 'hi' }, { name: 'no id' }] })
  assert.equal(o.requests, 2)
  assert.equal(o.people.length, 1)
  assert.equal(o.people[0]!.unread, 3)
  assert.equal(o.people[0]!.avatar_url, null)
  assert.deepEqual(normalizeOverview(null), { requests: 0, incoming: [], people: [] })
  const th = normalizeThread({ can_send: true, messages: [{ id: 2, from_me: true, body: 'b' }, { id: 1, deleted: true, body: null }] })
  assert.equal(th.can_send, true)
  assert.equal(th.messages[1]!.deleted, true)
})

test('polled messages merge by id, oldest first', () => {
  const m = (id: number, body = String(id)) => ({ id, from_me: false, body, deleted: false, created_at: '', read: false })
  assert.deepEqual(mergeMessages([m(1), m(3)], [m(3, 'x'), m(2), m(4)]).map(x => [x.id, x.body]), [[1, '1'], [2, '2'], [3, 'x'], [4, '4']])
})

test('times are short', () => {
  const now = new Date(2026, 9, 5, 15, 0)
  assert.equal(shortTime(new Date(2026, 9, 5, 9, 7).toISOString(), now), '09:07')
  assert.equal(shortTime(new Date(2026, 8, 30, 21, 5).toISOString(), now), '09-30 21:05')
  assert.equal(shortTime(new Date(2025, 0, 2).toISOString(), now), '2025-01-02')
  assert.equal(shortTime('nope', now), '')
})

test('the button hides under popups and the guide, never under its own panel', () => {
  assert.match(FAB_HIDE, /dialog\[open\]:not\(\.friends-fab-panel\)/)
  assert.match(FAB_HIDE, /compete-guide/)
})

test('zh and en have the same message keys (fr/ja fall back to en)', () => {
  const keys = (o: object, p = ''): string[] => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' ? keys(v, `${p}${k}.`) : [`${p}${k}`]))
  assert.deepEqual(keys((zh as Record<string, object>).dm).sort(), keys((en as Record<string, object>).dm).sort())
  for (const code of ['not_friends', 'empty', 'too_long', 'rate_minute', 'rate_day']) assert.ok((en as any).dm.errors[code] && (zh as any).dm.errors[code])
})
