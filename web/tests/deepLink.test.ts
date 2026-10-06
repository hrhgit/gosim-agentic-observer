import assert from 'node:assert/strict'
import test from 'node:test'
import { DEEP_LINKS, deepLinkFor, tabFromQuery } from '../src/lib/deepLink.ts'
import { shouldAutoShow } from '../src/lib/competeGuide.ts'

test('every documented anchor resolves on its page and nowhere else', () => {
  const documented: Record<string, string[]> = {
    '/compete': ['upload', 'keys', 'final', 'phase'],
    '/profile': ['uid', 'friends', 'wechat-qr'],
    '/team': ['invite-uid', 'requests'],
  }
  assert.deepEqual(Object.fromEntries(Object.entries(DEEP_LINKS).map(([p, m]) => [p, Object.keys(m)])), documented)
  for (const [page, keys] of Object.entries(documented)) for (const k of keys) assert.ok(deepLinkFor(page, '#' + k)?.targets.length, `${page}#${k}`)
  assert.equal(deepLinkFor('/profile', '#keys'), null)
  assert.equal(deepLinkFor('/compete', '#uid'), null)
  assert.equal(deepLinkFor('/compete', ''), null)
  assert.equal(deepLinkFor('/compete', '#batch-123'), null)
  assert.equal(deepLinkFor('/compete', '#toString'), null)
})

test('anchors open the right 参赛 tab and tolerate case and trailing slashes', () => {
  assert.equal(deepLinkFor('/compete/', '#KEYS')?.tab, 'settings')
  assert.equal(deepLinkFor('/compete', '#final')?.tab, 'settings')
  assert.equal(deepLinkFor('/compete', '#upload')?.tab, 'progress')
  assert.equal(deepLinkFor('/compete', '#phase')?.tab, undefined)
  assert.deepEqual(deepLinkFor('/compete', '#upload')?.reveal, ['[data-testid="project-upload-open"]'])
})

test('?tab= accepts only known tabs', () => {
  assert.equal(tabFromQuery('settings'), 'settings')
  assert.equal(tabFromQuery(['history']), 'history')
  for (const v of ['Settings', 'x', '', null, undefined, 3]) assert.equal(tabFromQuery(v), null)
})

test('the first-visit guide never covers a deep link', () => {
  assert.equal(shouldAutoShow('v2', new Set(), true), false)
  assert.equal(shouldAutoShow('v2', new Set(), false), true)
})
