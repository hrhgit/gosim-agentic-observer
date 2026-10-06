import assert from 'node:assert/strict'
import test from 'node:test'
import { LAND_PHASE, lightsTargets, shownSettled, storyFor, type StoryInput } from '../src/lib/replayStory.ts'

const base: StoryInput = {
  loaded: true, ended: false, kind: 'observe', frac: 0.5, lapse: false, sunUp: false, open: true,
  action: { hits: 13, required: 0, score: 8.6239, ordinal: 6 }, nightNo: 1, nights: 7,
  totals: { done: 2557, total: 2557, score: 2134.4 },
}

test('an exposure says how many fibres landed and what it earned', () => {
  assert.deepEqual(storyFor(base), { key: 'observe', params: { night: 1, nights: 7, n: 13, r: 0, score: '8.6', k: 6 } })
  assert.equal(storyFor({ ...base, action: { ...base.action!, required: 2 } }).key, 'observe_required')
})

test('an exposure taken with the dome shut is called out, not celebrated', () => {
  assert.equal(storyFor({ ...base, open: false, action: { ...base.action!, score: 0 } }).key, 'observe_blocked')
})

test('a skipped daytime reads as dawn, day, then dusk', () => {
  const gap = { ...base, kind: 'gap' as const, action: null, lapse: true }
  assert.equal(storyFor({ ...gap, frac: 0.1 }).key, 'dawn')
  assert.equal(storyFor({ ...gap, frac: 0.4, sunUp: true }).key, 'day')
  assert.equal(storyFor({ ...gap, frac: 0.9 }).key, 'dusk')
})

test('waiting inside a night distinguishes cloud from choice, and the end sums up', () => {
  const gap = { ...base, kind: 'gap' as const, action: null }
  assert.equal(storyFor({ ...gap, open: false }).key, 'closed')
  assert.equal(storyFor(gap).key, 'wait')
  assert.deepEqual(storyFor({ ...base, ended: true }).params, { night: 1, nights: 7, done: 2557, total: 2557, score: '2134.4' })
  assert.equal(storyFor({ ...base, loaded: false }).key, 'loading')
})

test('an exposure the scorer paid nothing for does not light its targets', () => {
  assert.equal(lightsTargets({ a: 'observe', cls: 'completed', score: 8.6, penalty: 0 }), true)
  assert.equal(lightsTargets({ a: 'observe', cls: 'completed', score: 0, penalty: 0 }), false)
  assert.equal(lightsTargets({ a: 'wait', cls: 'wait', score: 0, penalty: 0 }), false)
})

test('an exposure counts from the moment its fibres land, so score, lit targets and caption agree', () => {
  assert.equal(shownSettled(9, null), 9)
  assert.equal(shownSettled(9, { index: 9, phase: LAND_PHASE - 0.01 }), 9)
  assert.equal(shownSettled(9, { index: 9, phase: LAND_PHASE }), 10)
  // Never counts backwards when the clock has already finished it.
  assert.equal(shownSettled(12, { index: 9, phase: 0.9 }), 12)
})
