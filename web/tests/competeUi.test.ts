import assert from 'node:assert/strict'
import test from 'node:test'
import { competeLayout, layoutOverride, parseCompeteUi } from '../src/lib/competeUi.ts'

test('the switch defaults to the classic layout', () => {
  for (const v of [null, undefined, 1, 'x', {}, { compete_ui: null }, { compete_ui: { v2_all: 'true', v2_teams: 'a' } }])
    assert.equal(competeLayout(parseCompeteUi(v), 'a', null), 'classic')
})

test('listed teams, or everyone, get the new layout', () => {
  const s = parseCompeteUi({ other: 1, compete_ui: { v2_teams: ['t1', 2] } })
  assert.deepEqual(s.v2_teams, ['t1'])
  assert.equal(competeLayout(s, 't1', null), 'v2')
  assert.equal(competeLayout(s, 't2', null), 'classic')
  assert.equal(competeLayout(s, null, null), 'classic')
  assert.equal(competeLayout(parseCompeteUi({ compete_ui: { v2_all: true } }), null, null), 'v2')
})

test('a person can force a layout for themselves', () => {
  assert.equal(layoutOverride('v2', null), 'v2')
  assert.equal(layoutOverride(null, 'v2'), 'v2')
  assert.equal(layoutOverride('v1', 'v2'), 'classic')
  assert.equal(layoutOverride('auto', 'v2'), null)
  assert.equal(layoutOverride(null, null), null)
  assert.equal(competeLayout(parseCompeteUi({ compete_ui: { v2_all: true } }), 't', 'classic'), 'classic')
})
