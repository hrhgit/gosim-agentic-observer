import assert from 'node:assert/strict'
import test from 'node:test'
import { mainNavItems, moreNavItems, participateItem } from '../src/lib/nav.ts'

test('the header keeps its main links, ends with About, plus the prominent Participate button', () => {
  assert.deepEqual(mainNavItems.map(item => item.to), ['/start', '/rules', '/leaderboard', '/teammates', '/about'])
  assert.deepEqual(participateItem, { key: 'nav.submit', to: '/compete' })
})

test('every former header page stays reachable under More', () => {
  assert.deepEqual(moreNavItems.map(item => item.to),
    ['/brief', '/cards', '/docs', '/resources', '/faq', '/announcements'])
  const all = new Set([...mainNavItems, participateItem, ...moreNavItems].map(item => item.to))
  for (const path of ['/about', '/start', '/brief', '/rules', '/cards', '/docs', '/resources', '/faq', '/leaderboard', '/announcements', '/teammates', '/compete']) {
    assert.ok(all.has(path), `${path} must stay reachable from the header`)
  }
})

test('a narrow header folds Find teammates and About first, then Rules, then Leaderboard', () => {
  const order = [...mainNavItems].filter(item => item.fold).sort((a, b) => a.fold! - b.fold!).map(item => item.to)
  assert.deepEqual(order, ['/teammates', '/about', '/rules', '/leaderboard'])
  assert.equal(mainNavItems.find(item => item.to === '/start')?.fold, undefined)
})
