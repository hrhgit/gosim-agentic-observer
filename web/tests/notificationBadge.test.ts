import assert from 'node:assert/strict'
import test from 'node:test'
import { badgeText, bellCount, bellLabel, bellTarget, pendingTotal } from '../src/lib/notificationBadge.ts'

test('friend requests count with team requests until handled; unread only when nothing waits', () => {
  assert.equal(pendingTotal({ team: 1, friends: 2, unread: 5 }), 3)
  assert.equal(bellCount({ team: 0, friends: 2, unread: 5 }), 2)
  assert.equal(bellCount({ team: 0, friends: 0, unread: 5 }), 5)
  assert.equal(bellCount({ team: 0, friends: 0, unread: 0 }), 0)
  assert.equal(badgeText(120), '99+')
})

test('the bell leads to what is waiting', () => {
  assert.equal(bellTarget({ team: 1, friends: 1, unread: 0 }), '/team#requests')
  assert.equal(bellTarget({ team: 0, friends: 1, unread: 0 }), '/profile#friends')
  assert.equal(bellTarget({ team: 0, friends: 0, unread: 3 }), '/notifications')
  assert.equal(bellLabel({ team: 0, friends: 1, unread: 0 }).zh, '1 条好友请求待处理')
  assert.equal(bellLabel({ team: 2, friends: 1, unread: 0 }).en, '2 team requests and 1 friend request waiting for you')
  assert.equal(bellLabel({ team: 0, friends: 0, unread: 0 }).en, 'Team notifications')
})
