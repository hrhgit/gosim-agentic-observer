import assert from 'node:assert/strict'
import test from 'node:test'
import { evaluateBlock, latestFailure } from '../src/lib/projectEvaluation.ts'

const b = (id: string, at: string, status: string, runs: string[] = []) =>
  ({ id, created_at: at, status, observer_runs: runs.map((s, i) => ({ id: `${id}-${i}`, status: s })) })

test('only the newest evaluation is reported as a failure', () => {
  assert.equal(latestFailure([]), null)
  assert.equal(latestFailure([b('a', '2026-10-04T03:00', 'scored', ['scored']), b('x', '2026-10-04T01:00', 'failed')]), null)
  const f = latestFailure([b('a', '2026-10-04T03:00', 'scored'), b('x', '2026-10-04T08:00', 'failed', ['failed', 'failed'])])
  assert.equal(f?.batch.id, 'x'); assert.equal(f?.run?.id, 'x-0')
  // A scored evaluation with one failed card still points at that card.
  assert.equal(latestFailure([b('s', '2026-10-04T09:00', 'scored', ['scored', 'failed'])])?.run?.id, 's-1')
  // An evaluation still running is not a failure yet.
  assert.equal(latestFailure([b('r', '2026-10-04T09:00', 'running', ['failed'])]), null)
})

test('the disabled evaluate button names one reason, the most actionable first', () => {
  const ok = { busy: false, phaseEnabled: true, quota: { phase_id: 'p', daily_batches: 40, used: 0, remaining: 40 }, activeAtLimit: false }
  assert.equal(evaluateBlock(ok), null)
  assert.equal(evaluateBlock({ ...ok, phaseEnabled: false }), 'phase_closed')
  assert.equal(evaluateBlock({ ...ok, quota: { ...ok.quota, remaining: 0 }, activeAtLimit: true }), 'no_quota')
  assert.equal(evaluateBlock({ ...ok, activeAtLimit: true, busy: true }), 'active_limit')
  assert.equal(evaluateBlock({ ...ok, busy: true }), 'busy')
  assert.equal(evaluateBlock({ ...ok, selfCheckActive: true }), null)
  assert.equal(evaluateBlock({ ...ok, selfCheckActive: true }, true), 'self_check_running')
  assert.equal(evaluateBlock({ ...ok, quota: { ...ok.quota, remaining: 2 } }, true), 'self_check_quota')
  assert.equal(evaluateBlock({ ...ok, quota: null }), null)
})
