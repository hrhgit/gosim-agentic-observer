import assert from 'node:assert/strict'
import test from 'node:test'
import { questProgress, questStorageKey, readQuestFlags, rememberQuestFlag, type QuestSignals, type StorageLike } from '../src/lib/quest.ts'

const signals = (over: Partial<QuestSignals> = {}): QuestSignals => ({ hasTeam: false, prepared: false, submitted: false, reviewed: false, ...over })
const state = (over: Partial<QuestSignals>) => questProgress(signals(over)).steps.map(step => step.done ? 'done' : step.current ? 'current' : 'todo')

test('a new player starts on the team step with nothing done', () => {
  const progress = questProgress(signals())
  assert.equal(progress.done, 0)
  assert.equal(progress.total, 4)
  assert.equal(progress.current, 'team')
  assert.deepEqual(state({}), ['current', 'todo', 'todo', 'todo'])
})

test('exactly one step is current until everything is done', () => {
  for (const hasTeam of [false, true]) for (const prepared of [false, true]) for (const submitted of [false, true]) for (const reviewed of [false, true]) {
    const progress = questProgress({ hasTeam, prepared, submitted, reviewed })
    const current = progress.steps.filter(step => step.current)
    assert.equal(current.length, progress.finished ? 0 : 1)
    if (current[0]) assert.equal(progress.steps.findIndex(step => !step.done), progress.steps.indexOf(current[0]))
  }
})

test('steps advance with the data', () => {
  assert.deepEqual(state({ hasTeam: true }), ['done', 'current', 'todo', 'todo'])
  assert.deepEqual(state({ hasTeam: true, prepared: true }), ['done', 'done', 'current', 'todo'])
  assert.deepEqual(state({ hasTeam: true, prepared: true, submitted: true }), ['done', 'done', 'done', 'current'])
  const finished = questProgress(signals({ hasTeam: true, prepared: true, submitted: true, reviewed: true }))
  assert.equal(finished.finished, true)
  assert.equal(finished.done, 4)
  assert.equal(finished.current, null)
})

test('a submission proves the kit step; remembered clicks never skip the team', () => {
  assert.deepEqual(state({ hasTeam: true, submitted: true }), ['done', 'done', 'done', 'current'])
  assert.deepEqual(state({ prepared: true }), ['current', 'done', 'todo', 'todo'])
  // Submissions belong to a team: without one nothing counts as submitted or reviewed.
  assert.deepEqual(state({ submitted: true, reviewed: true }), ['current', 'todo', 'todo', 'todo'])
  assert.deepEqual(state({ hasTeam: true, prepared: true, reviewed: true }), ['done', 'done', 'current', 'todo'])
})

function memoryStorage(): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>()
  return { data, getItem: key => data.get(key) ?? null, setItem: (key, value) => { data.set(key, value) } }
}

test('remembered steps are kept per user and per mode', () => {
  const storage = memoryStorage()
  assert.deepEqual(readQuestFlags(storage, 'u1', 'practice'), { prepare: false, review: false })
  rememberQuestFlag(storage, 'u1', 'practice', 'prepare')
  rememberQuestFlag(storage, 'u1', 'practice', 'review')
  assert.deepEqual(readQuestFlags(storage, 'u1', 'practice'), { prepare: true, review: true })
  assert.deepEqual(readQuestFlags(storage, 'u1', 'competition'), { prepare: false, review: false })
  assert.deepEqual(readQuestFlags(storage, 'u2', 'practice'), { prepare: false, review: false })
  assert.equal(storage.data.get(questStorageKey('u1', 'practice')), '{"prepare":true,"review":true}')
})

test('blocked or corrupt storage never breaks the dashboard', () => {
  const blocked: StorageLike = { getItem: () => { throw new Error('SecurityError') }, setItem: () => { throw new Error('QuotaExceededError') } }
  assert.deepEqual(readQuestFlags(blocked, 'u1', 'practice'), {})
  assert.deepEqual(rememberQuestFlag(blocked, 'u1', 'practice', 'prepare'), { prepare: true })
  assert.deepEqual(readQuestFlags(null, 'u1', 'practice'), { prepare: false, review: false })
  const corrupt = memoryStorage()
  corrupt.data.set(questStorageKey('u1', 'practice'), '{not json')
  assert.deepEqual(readQuestFlags(corrupt, 'u1', 'practice'), {})
  corrupt.data.set(questStorageKey('u1', 'practice'), 'null')
  assert.deepEqual(readQuestFlags(corrupt, 'u1', 'practice'), { prepare: false, review: false })
})
