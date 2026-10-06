import assert from 'node:assert/strict'
import test from 'node:test'
import { actionable, blockedText, normalizeInbox, parseSeen, rememberSeen, unseenActionable } from '../src/lib/teamInbox.ts'

const row = (id: string, extra: Record<string, unknown> = {}) => ({ id, kind: 'request', direction: 'received', team_id: 't', team_name: 'T',
  sender_name: 'A', recipient_name: 'B', status: 'pending', created_at: '2026-10-04T08:00:00Z', updated_at: '2026-10-04T08:00:00Z', blocked: null, ...extra })

test('the backend payload is normalized defensively', () => {
  assert.deepEqual(normalizeInbox(null), { received: [], sent: [] })
  assert.deepEqual(normalizeInbox({ received: [null, { id: 1 }, row('a')], sent: 'x' }), { received: [row('a')], sent: [] })
})

test('only waiting rows the person can act on count and pop up, each once', () => {
  const inbox = normalizeInbox({ received: [row('a'), row('b', { blocked: 'joined_other_team' }), row('c')], sent: [row('d', { direction: 'sent' })] })
  assert.deepEqual(actionable(inbox).map(r => r.id), ['a', 'c'])
  const seen = rememberSeen(null, ['a'])
  assert.deepEqual(unseenActionable(inbox, parseSeen(seen)).map(r => r.id), ['c'])
  assert.deepEqual(unseenActionable(inbox, parseSeen(rememberSeen(seen, ['c']))), [])
  assert.deepEqual([...parseSeen('not json')], [])
})

test('blocked reasons read as text', () => {
  assert.equal(blockedText(null), null)
  assert.equal(blockedText('joined_other_team')?.zh, '对方已加入其他队伍')
  assert.ok(blockedText('something_new')?.en)
})
