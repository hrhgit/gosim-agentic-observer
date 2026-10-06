import assert from 'node:assert/strict'
import test from 'node:test'
import { byUidOutcome, normalizeFriends, parseUid } from '../src/lib/friends.ts'

test('a UID is read as typed, but only a 9-digit one counts', () => {
  assert.equal(parseUid('100000123'), 100000123)
  assert.equal(parseUid(' UID 100 000 123 '), 100000123)
  assert.equal(parseUid('uid:100000123'), 100000123)
  for (const bad of ['', '12345678', '1000001234', '012345678', 'abc', '10000012a']) assert.equal(parseUid(bad), null)
})

test('by-UID answers become a status or a thrown error code', () => {
  assert.equal(byUidOutcome({ status: 'sent', request_id: 'x' }), 'sent')
  assert.throws(() => byUidOutcome({ error: 'uid_unavailable' }), /uid_unavailable/)
  assert.throws(() => byUidOutcome(null), /generic/)
})

test('the friends payload is normalized defensively', () => {
  const n = normalizeFriends({ uid: 100000001, friends: [{ user_id: 'a' }], incoming: 'x' })
  assert.equal(n.uid, 100000001)
  assert.equal(n.friends.length, 1)
  assert.deepEqual(n.incoming, [])
  assert.equal(normalizeFriends(null).daily_limit, 20)
})

test('a find-by-UID answer gives the actions, or one neutral error', async () => {
  const { foundExtra } = await import('../src/lib/friends.ts')
  assert.deepEqual(foundExtra({ id: 'u', uid: 100000002, self: false, is_friend: true, in_team: false }),
    { uid: 100000002, self: false, is_friend: true, in_team: false })
  assert.throws(() => foundExtra({ error: 'not_found' }), /not_found/)
  assert.throws(() => foundExtra({ error: 'daily_limit' }), /daily_limit/)
  assert.throws(() => foundExtra(null), /not_found/)
})
