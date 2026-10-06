import assert from 'node:assert/strict'
import test from 'node:test'
import { scenarioLabel, scenarioOrder } from '../src/lib/scenarioLabels.ts'

test('practice cards are localized from the slug, never the DB name', () => {
  assert.equal(scenarioLabel('v4-practice-alpha', 'Practice card α', 'en'), 'Practice card α')
  assert.equal(scenarioLabel('v4-practice-alpha', 'Practice card α', 'zh'), '练习卡 α')
  assert.equal(scenarioLabel('v4-practice-delta', 'Practice card α', 'zh'), '练习卡 δ')
})

test('hackathon task cards A-D are localized from the slug', () => {
  assert.equal(scenarioLabel('v4-a', 'Card A', 'en'), 'Card A')
  assert.equal(scenarioLabel('v4-a', 'Card A', 'zh'), '任务卡 A')
  assert.equal(scenarioLabel('v4-d', 'anything', 'zh'), '任务卡 D')
})

test('non-en locales fall back to the English form, matching the rest of the site', () => {
  assert.equal(scenarioLabel('v4-practice-beta', 'Practice card β', 'ja'), 'Practice card β')
  assert.equal(scenarioLabel('v4-b', 'Card B', 'fr'), 'Card B')
})

test('unknown slugs keep showing the DB name untouched', () => {
  assert.equal(scenarioLabel('legacy-scenario', 'Legacy Scenario', 'zh'), 'Legacy Scenario')
  assert.equal(scenarioLabel('v4-z', 'Hidden Z', 'zh'), 'Hidden Z')
})

test('scenarioOrder sorts practice cards alpha/beta/gamma/delta, not slug text (which puts delta before gamma)', () => {
  const slugs = ['v4-practice-delta', 'v4-practice-gamma', 'v4-practice-alpha', 'v4-practice-beta']
  assert.deepEqual([...slugs].sort((a, b) => scenarioOrder(a) - scenarioOrder(b)),
    ['v4-practice-alpha', 'v4-practice-beta', 'v4-practice-gamma', 'v4-practice-delta'])
})

test('scenarioOrder sorts hackathon cards A, B, C, D', () => {
  const slugs = ['v4-c', 'v4-a', 'v4-d', 'v4-b']
  assert.deepEqual([...slugs].sort((a, b) => scenarioOrder(a) - scenarioOrder(b)), ['v4-a', 'v4-b', 'v4-c', 'v4-d'])
})

test('scenarioOrder keeps unrecognized slugs in their original relative order (stable sort, all tie at Infinity)', () => {
  const slugs = ['legacy-b', 'legacy-a']
  assert.deepEqual([...slugs].sort((a, b) => scenarioOrder(a) - scenarioOrder(b)), ['legacy-b', 'legacy-a'])
})
