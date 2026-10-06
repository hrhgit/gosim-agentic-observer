import assert from 'node:assert/strict'
import test from 'node:test'
import { entryPhaseId, entryPhaseIds, entryStorageKey, offersPracticeSwitch, parseCompetition, readEntryChoice, rememberEntryChoice, type CompetitionState } from '../src/lib/entryPhase.ts'

const memory = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v) }, m } }
const state = (data: unknown, beta: string | null = null): CompetitionState => ({ ...parseCompetition(data), betaPhaseId: beta })

test('practice mode answers exactly as before and offers no switch', () => {
  const s = state({ mode: 'practice', phase_id: 'csv', project_phase_id: 'pp', practice_phase_id: 'ignored' })
  assert.deepEqual(s, { mode: 'practice', phaseId: 'csv', projectPhaseId: 'pp', practicePhaseId: null, betaPhaseId: null })
  assert.equal(offersPracticeSwitch(s), false)
  assert.equal(entryPhaseId(s, 'practice'), 'pp')
  assert.equal(entryPhaseId(s, 'online'), 'pp')
})

test('competition mode defaults to the online phase and keeps practice one click away', () => {
  const s = state({ mode: 'competition', phase_id: 'online', practice_phase_id: 'pp' })
  assert.equal(offersPracticeSwitch(s), true)
  assert.equal(entryPhaseId(s, 'online'), 'online')
  assert.equal(entryPhaseId(s, 'practice'), 'pp')
  assert.deepEqual(entryPhaseIds(s), ['online', 'pp'])
  // A team-restricted beta entry still wins for the online choice.
  assert.equal(entryPhaseId(state({ mode: 'competition', phase_id: 'online', practice_phase_id: 'pp' }, 'beta'), 'online'), 'beta')
})

test('without an open practice board the competition has no switch and stays online', () => {
  const s = state({ mode: 'competition', phase_id: 'online' })
  assert.equal(offersPracticeSwitch(s), false)
  assert.equal(entryPhaseId(s, 'practice'), 'online')
  assert.deepEqual(state(null), { mode: 'practice', phaseId: null, projectPhaseId: null, practicePhaseId: null, betaPhaseId: null })
})

test('the choice is remembered per user and defaults to online', () => {
  const st = memory()
  assert.equal(readEntryChoice(st, 'u1'), 'online')
  rememberEntryChoice(st, 'u1', 'practice')
  assert.equal(st.m.get(entryStorageKey('u1')), 'practice')
  assert.equal(readEntryChoice(st, 'u1'), 'practice')
  assert.equal(readEntryChoice(st, 'u2'), 'online')
  assert.equal(readEntryChoice(null, 'u1'), 'online')
  assert.equal(readEntryChoice({ getItem: () => { throw new Error('blocked') }, setItem: () => {} }, 'u1'), 'online')
  rememberEntryChoice(st, null, 'practice')
  assert.equal(st.m.size, 1)
})

test('after the online deadline the page opens on practice, whatever was remembered', async () => {
  const { initialEntryChoice } = await import('../src/lib/entryPhase.ts')
  const st = memory()
  rememberEntryChoice(st, 'u1', 'online')
  assert.equal(initialEntryChoice(st, 'u1', false), 'online')
  assert.equal(initialEntryChoice(st, 'u1', true), 'practice')
  assert.equal(initialEntryChoice(null, null, true), 'practice')
  // The server answers project_phase_id = practice once online has ended; online stays viewable.
  const s = state({ mode: 'competition', phase_id: 'online', practice_phase_id: 'pp', project_phase_id: 'pp' })
  assert.equal(offersPracticeSwitch(s), true)
  assert.equal(entryPhaseId(s, 'practice'), 'pp')
  assert.equal(entryPhaseId(s, 'online'), 'online')
  assert.deepEqual(entryPhaseIds(s), ['online', 'pp', 'pp'])
})
