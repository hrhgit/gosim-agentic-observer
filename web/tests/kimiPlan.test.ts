import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { kimiPlanPopupRole, kimiPlanSoldOutAfterClaim, kimiPlanState, normalizeKimiPlanStatus } from '../src/lib/kimiPlan.ts'

const base = { has_team: true, is_captain: true, hidden: false, qualified: true, eligible: true, imported: true, available: 3, code: null, note: '', claimed_at: null, claimed_by: null }

test('the backend payload is normalized defensively', () => {
  const s = normalizeKimiPlanStatus({ has_team: true, available: '2', code: '', claimed_by: '' })
  assert.equal(s.available, 2)
  assert.equal(s.code, null)
  assert.equal(s.claimed_by, null)
  assert.equal(s.eligible, false)
  assert.equal(normalizeKimiPlanStatus(null).has_team, false)
})

test('before codes are imported the panel says they are coming, whatever the eligibility', () => {
  assert.equal(kimiPlanState({ ...base, imported: false, available: 0 }), 'coming_soon')
  assert.equal(kimiPlanState({ ...base, imported: false, available: 0, eligible: false }), 'coming_soon')
})

test('only the captain of an eligible team gets the claim button', () => {
  assert.equal(kimiPlanState(base), 'claimable')
  assert.equal(kimiPlanState({ ...base, is_captain: false }), 'wait_captain')
  assert.equal(kimiPlanState({ ...base, eligible: false }), 'not_eligible')
  assert.equal(kimiPlanState({ ...base, available: 0 }), 'sold_out')
})

test('a claimed code is shown to every member, even after the pool runs out', () => {
  assert.equal(kimiPlanState({ ...base, code: 'k-1', available: 0, is_captain: false }), 'claimed')
  assert.equal(kimiPlanState({ ...base, has_team: false }), 'no_team')
})

test('once the pool is empty, teams without a code get the sold-out notice, no popup and no claim button', () => {
  const empty = { ...base, available: 0 }
  assert.equal(kimiPlanState(empty), 'sold_out')
  assert.equal(kimiPlanState({ ...empty, is_captain: false }), 'sold_out')
  assert.equal(kimiPlanState({ ...empty, eligible: false }), 'sold_out')
  assert.equal(kimiPlanPopupRole(empty), null)
  assert.equal(kimiPlanPopupRole({ ...empty, is_captain: false }), null)
  assert.equal(kimiPlanPopupRole({ ...empty, code: 'k-1' }), null)
  assert.equal(kimiPlanPopupRole(base), 'captain')
  assert.equal(kimiPlanPopupRole({ ...base, is_captain: false }), 'member')
  assert.equal(kimiPlanPopupRole({ ...base, imported: false }), null)
})

test('a claim that loses the race to the last code switches to the sold-out notice instead of an error', () => {
  const after = kimiPlanSoldOutAfterClaim(base, ' no_codes_left ')
  assert.ok(after)
  assert.equal(kimiPlanState(after), 'sold_out')
  assert.equal(kimiPlanSoldOutAfterClaim(base, 'captain_only'), null)
})

test('the sold-out wording is calm and the same for the notice and the late-claim error', () => {
  for (const lang of ['zh', 'en']) {
    const k = JSON.parse(readFileSync(new URL(`../src/i18n/${lang}.json`, import.meta.url), 'utf8')).kimi_plan
    assert.equal(k.errors.no_codes_left, k.sold_out)
    assert.doesNotMatch(k.sold_out, /组委会|contact|organi[sz]er/i)
  }
  const zh = JSON.parse(readFileSync(new URL('../src/i18n/zh.json', import.meta.url), 'utf8')).kimi_plan
  assert.equal(zh.sold_out, 'Kimi Coding Plan 兑换码已全部发放完毕（前一百支队伍，先到先得）。')
})
