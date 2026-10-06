import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { FORMAL_CARDS, PRACTICE_CARDS, cardPagePath, cardTitle, cardZipEntry, findCard, practiceCardsWithPages } from '../src/lib/taskCards.ts'

test('the site knows the practice cards and the hackathon cards A–D, never the hidden cards E–H', () => {
  assert.deepEqual(PRACTICE_CARDS.map(c => [c.id, c.slug, c.symbol]),
    [['alpha', 'v4-practice-alpha', 'α'], ['beta', 'v4-practice-beta', 'β'], ['gamma', 'v4-practice-gamma', 'γ'], ['delta', 'v4-practice-delta', 'δ']])
  assert.deepEqual(FORMAL_CARDS.map(c => [c.id, c.slug, c.symbol]), [['a', 'v4-a', 'A'], ['b', 'v4-b', 'B'], ['c', 'v4-c', 'C'], ['d', 'v4-d', 'D']])
  for (const hidden of ['e', 'f', 'g', 'h', 'v4-e']) assert.equal(findCard(hidden), null)
  assert.equal(findCard('a')?.stage, 'formal')
  assert.equal(findCard('alpha')?.stage, 'practice')
})

test('only practice cards with a bundled page are offered, in card order', () => {
  assert.deepEqual(practiceCardsWithPages([
    '../content/taskcard.beta.v4.en.md', '../content/taskcard.alpha.v4.zh.md', '../content/taskcard.template.v4.zh.md',
  ]).map(c => c.id), ['alpha', 'beta'])
  assert.deepEqual(practiceCardsWithPages([]), [])
})

test('the bundled card pages are exactly the practice cards (no formal or hidden card page can ship)', () => {
  const source = readFileSync(new URL('../src/lib/taskCardSource.ts', import.meta.url), 'utf8')
  const globs = [...source.matchAll(/import\.meta\.glob\((['"`])([^'"`]+)\1/g)].map(m => m[2])
  assert.deepEqual(globs, ['../content/taskcard.{alpha,beta,gamma,delta}.v4.{zh,en}.md'])
  const stems = readdirSync(new URL('../src/content/', import.meta.url)).map(name => /^taskcard\.([a-z]+)\.v4\.(zh|en)\.md$/.exec(name)?.[1]).filter(Boolean)
  for (const stem of stems) assert.ok(stem === 'template' || PRACTICE_CARDS.some(c => c.id === stem), `unexpected card page ${stem}`)
})

test('released card files map into cards/<id>/… and nothing else gets into the ZIP', () => {
  const alpha = findCard('alpha')!
  assert.equal(cardZipEntry(alpha, 'config/v4_scenario.json'), 'alpha/config/v4_scenario.json')
  assert.equal(cardZipEntry(alpha, 'truth/v4_weather_truth.csv'), 'alpha/truth/v4_weather_truth.csv')
  for (const bad of ['outputs/reference/weather.csv', 'config/../x', 'public/a/b.csv', '../v4-e/config/x.json', 'public/']) {
    assert.equal(cardZipEntry(alpha, bad), null, bad)
  }
  assert.equal(cardPagePath('zh'), 'public/taskcard.zh.md')
})

test('a card title is the first heading of its page', () => {
  const alpha = findCard('alpha')!
  assert.equal(cardTitle('<!-- note -->\n# 任务卡 α（alpha）：初见星光\n\ntext\n## 一览', alpha), '任务卡 α（alpha）：初见星光')
  assert.equal(cardTitle(null, findCard('b')!), 'B')
})
