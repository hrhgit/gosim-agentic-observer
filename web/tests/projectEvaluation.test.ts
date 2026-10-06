import assert from 'node:assert/strict'
import test from 'node:test'
import { canChooseFinal, canClearFinal, canWithdraw, countedEvaluations, finalRole, finalVersionFor, recentDuplicate, visibleProjects, withdrawnCount } from '../src/lib/projectEvaluation.ts'

const batches = [
  { revision_id: 'a', phase_id: 'p', status: 'scored', quota_refunded: false },
  { revision_id: 'a', phase_id: 'p', status: 'failed', quota_refunded: true },
  { revision_id: 'a', phase_id: 'other', status: 'scored' },
  { revision_id: 'b', phase_id: 'p', status: 'failed', quota_refunded: true },
]

test('only counted evaluations of the same version and phase make a repeat', () => {
  assert.equal(countedEvaluations(batches, 'a', 'p'), 1)
  assert.equal(countedEvaluations(batches, 'b', 'p'), 0)
  assert.equal(countedEvaluations(null, 'a', 'p'), 0)
})

test('evaluated, withdrawn and preparing versions cannot be withdrawn', () => {
  for (const status of ['queued', 'reviewable', 'failed', 'approved']) assert.ok(canWithdraw({ id: 'c', status }, batches), status)
  assert.ok(!canWithdraw({ id: 'c', status: 'preparing' }, batches))
  assert.ok(!canWithdraw({ id: 'b', status: 'approved' }, batches))
  assert.ok(!canWithdraw({ id: 'c', status: 'approved', archived_at: '2026-09-27T00:00:00Z' }, batches))
})

test('withdrawn versions are hidden until requested', () => {
  const projects = [
    { title: 'One', observer_revisions: [{ id: '1', status: 'approved', archived_at: '2026-09-27T00:00:00Z' }] },
    { title: 'Two', observer_revisions: [{ id: '2', status: 'reviewable' }] },
  ]
  assert.deepEqual(visibleProjects(projects, false).map(p => p.title), ['Two'])
  assert.deepEqual(visibleProjects(projects, true).map(p => p.title), ['One', 'Two'])
  assert.equal(withdrawnCount(projects), 1)
})

test('a resubmission of the same project within minutes is recognized', () => {
  const now = Date.parse('2026-09-27T10:00:00Z')
  const projects = [
    { title: 'Agent', observer_revisions: [{ id: '1', status: 'queued', source_kind: 'repository', source_location: 'https://github.com/Owner/Repo', created_at: '2026-09-27T09:57:00Z' }] },
    { title: 'Zip', observer_revisions: [{ id: '2', status: 'queued', source_kind: 'zip', source_location: 't/u/source.zip', created_at: '2026-09-27T09:58:00Z' }] },
  ]
  assert.ok(recentDuplicate(projects, ' Agent ', 'https://github.com/owner/repo.git/', now))
  assert.ok(!recentDuplicate(projects, 'Agent', 'https://github.com/owner/other', now))
  assert.ok(!recentDuplicate(projects, 'Other', 'https://github.com/owner/repo', now))
  assert.ok(!recentDuplicate(projects, 'Agent', 'https://github.com/owner/repo', now + 20 * 60_000))
  assert.ok(recentDuplicate(projects, 'Zip', null, now))
  assert.ok(!recentDuplicate(projects, 'Agent', null, now))
})

test('the final version: chosen or default, changeable until the deadline', () => {
  const base = { deadline: '2026-10-07T15:59:00Z', locked: false, chosen_by: null, chosen_at: null, best_batch_id: 'b1', best_revision_id: 'r1', best_score: 12 }
  const defaulted = { ...base, phase_id: 'online', revision_id: 'r1', source: 'best' as const, chosen_revision_id: null }
  const chosen = { ...base, phase_id: 'other', revision_id: 'r2', source: 'chosen' as const, chosen_revision_id: 'r2' }
  const before = Date.parse('2026-10-06T00:00:00Z'), after = Date.parse('2026-10-07T16:00:00Z')
  assert.equal(finalVersionFor([chosen, defaulted], 'online'), defaulted)
  assert.equal(finalVersionFor([chosen, defaulted], 'missing'), chosen)
  assert.equal(finalVersionFor(null, 'online'), null)
  assert.equal(finalRole(defaulted, 'r1'), 'best')
  assert.equal(finalRole(chosen, 'r2'), 'chosen')
  assert.equal(finalRole(chosen, 'r1'), null)
  assert.ok(canChooseFinal(defaulted, 'r1', before))
  assert.ok(!canChooseFinal(chosen, 'r2', before))
  assert.ok(canChooseFinal(chosen, 'r1', before))
  assert.ok(!canChooseFinal(chosen, 'r1', after))
  assert.ok(!canChooseFinal({ ...chosen, locked: true }, 'r1', before))
  assert.ok(canClearFinal(chosen, before) && !canClearFinal(defaulted, before) && !canClearFinal(chosen, after))
  assert.ok(!canChooseFinal(null, 'r1', before) && !canClearFinal(null, before))
})

test('a self-check shows the mean and range per card and overall over its scored evaluations', async () => {
  const { repeatSummaries, canSelfCheck } = await import('../src/lib/projectEvaluation.ts')
  const run = (scenario_id: string, score: number | null, status = 'scored') => ({ scenario_id, status, score })
  const summaries = repeatSummaries([
    { id: '1', status: 'scored', score: 20, repeat_group: 'g', repeat_runs: 3, observer_runs: [run('a', 10), run('b', 30)] },
    { id: '2', status: 'scored', score: 40, repeat_group: 'g', repeat_runs: 3, observer_runs: [run('a', 30), run('b', 50)] },
    { id: '3', status: 'running', score: null, repeat_group: 'g', repeat_runs: 3, observer_runs: [run('a', null, 'running'), run('b', 99)] },
    { id: '4', status: 'scored', score: 70, observer_runs: [run('a', 70)] },
  ])
  assert.equal(summaries.size, 1)
  const g = summaries.get('g')!
  assert.deepEqual([g.runs, g.scored, g.active], [3, 2, 1])
  assert.deepEqual(g.cards, [{ scenario_id: 'a', mean: 20, min: 10, max: 30 }, { scenario_id: 'b', mean: 40, min: 30, max: 50 }])
  assert.deepEqual(g.overall, { mean: 30, min: 20, max: 40 })
  assert.ok(canSelfCheck({ phase_id: 'p', daily_batches: 4, used: 1, remaining: 3 }))
  assert.ok(!canSelfCheck({ phase_id: 'p', daily_batches: 4, used: 2, remaining: 2 }))
})

test('a self-check set counts once toward the evaluations in flight', async () => {
  const { activeEvaluations } = await import('../src/lib/projectEvaluation.ts')
  assert.equal(activeEvaluations([
    { id: '1', status: 'running' }, { id: '2', status: 'queued' }, { id: '3', status: 'scored' },
    { id: '4', status: 'running', repeat_group: 'g' }, { id: '5', status: 'queued', repeat_group: 'g' },
  ]), 3)
  assert.equal(activeEvaluations(null), 0)
})

test('daily project preparations come from the quota rows, never a fixed number', async () => {
  const { preparationQuota } = await import('../src/lib/projectEvaluation.ts')
  assert.equal(preparationQuota(null), null)
  assert.equal(preparationQuota([{ phase_id: 'a', daily_batches: 40, used: 0, remaining: 40 }]), null)
  assert.deepEqual(preparationQuota([{ phase_id: 'a', daily_batches: 40, used: 0, remaining: 40, resets_at: 'r',
    preparations_daily: 40, preparations_used: 3, preparations_remaining: 37 }]), { daily: 40, used: 3, remaining: 37, resets_at: 'r' })
})

test('evaluations without a model are marked in the download name and metadata', async () => {
  const { evaluationMetadata, evaluationZipName, isNoModel } = await import('../src/lib/projectEvaluation.ts')
  const plain = { id: 'abcdef12-0000', created_at: 't', phase_id: 'p', revision_id: 'r' }
  const off = { ...plain, model_disabled: true, repeat_group: 'g' }
  assert.equal(isNoModel(plain), false)
  assert.equal(isNoModel(off), true)
  assert.equal(evaluationZipName(plain, '2026-10-05'), 'gosim-observer-abcdef12-2026-10-05.zip')
  assert.equal(evaluationZipName(off, '2026-10-05'), 'gosim-observer-abcdef12-no-model-2026-10-05.zip')
  assert.deepEqual(evaluationMetadata(off, 'v1'), { evaluation_id: 'abcdef12-0000', created_at: 't', phase_id: 'p',
    revision_id: 'r', version: 'v1', model_provided: false, model_disabled: true, self_check_group: 'g' })
  assert.equal(evaluationMetadata(plain, null).model_provided, true)
})
