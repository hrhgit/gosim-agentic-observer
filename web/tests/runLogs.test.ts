import assert from 'node:assert/strict'
import test from 'node:test'
import { agentLogFileName, agentRunOf, loadLogs } from '../src/lib/runLogs.ts'

const RUN = '30000000-0000-4000-8000-000000000001'
const REV = '30000000-0000-4000-8000-000000000002'

function fakePortal(answers: Record<string, unknown>) {
  const calls: [string, Record<string, unknown>][] = []
  const portal = async <T>(action: string, fields: Record<string, unknown>): Promise<T> => {
    calls.push([action, fields])
    const answer = answers[action]
    if (answer instanceof Error) throw answer
    return answer as T
  }
  return { portal, calls }
}

test('a run shows its diagnostics and its own agent.log', async () => {
  const diagnostics = [{ kind: 'engine', status: 'succeeded', code: 'succeeded', log: '' }]
  const agent = { available: true, bytes: 6, truncated: false, log: 'hello\n' }
  const { portal, calls } = fakePortal({ diagnostics, agent_log: agent })
  assert.deepEqual(await loadLogs(portal, { run_id: RUN }), { diagnostics, agentRun: RUN, agent })
  assert.deepEqual(calls, [['diagnostics', { run_id: RUN }], ['agent_log', { run_id: RUN }]])
})

test("a version shows its preparation diagnostics and its public test run's agent.log", async () => {
  const { portal, calls } = fakePortal({ diagnostics: [], agent_log: { available: false } })
  const logs = await loadLogs(portal, { revision_id: REV, test_run_id: RUN })
  assert.deepEqual(logs, { diagnostics: [], agentRun: RUN, agent: { available: false } })
  assert.deepEqual(calls, [['diagnostics', { revision_id: REV }], ['agent_log', { run_id: RUN }]])
  // No public test yet: diagnostics only.
  const none = fakePortal({ diagnostics: [] })
  assert.deepEqual(await loadLogs(none.portal, { revision_id: REV, test_run_id: null }), { diagnostics: [], agentRun: null, agent: null })
  assert.deepEqual(none.calls, [['diagnostics', { revision_id: REV }]])
  assert.equal(agentRunOf({ revision_id: REV }), null)
})

test('an unreadable agent.log never hides the diagnostics; a diagnostics failure is reported', async () => {
  const diagnostics = [{ kind: 'execute', status: 'failed', code: 'project_operation_failed', log: 'Traceback' }]
  const { portal } = fakePortal({ diagnostics, agent_log: new Error('portal_unavailable') })
  assert.deepEqual(await loadLogs(portal, { run_id: RUN }), { diagnostics, agentRun: RUN, agent: null })
  const broken = fakePortal({ diagnostics: new Error('diagnostics_not_found'), agent_log: { available: false } })
  await assert.rejects(loadLogs(broken.portal, { run_id: RUN }), /diagnostics_not_found/)
})

test('downloaded agent.log files are named after the card folder', () => {
  assert.equal(agentLogFileName('alpha-cluster'), 'alpha-cluster-agent.log')
  assert.equal(agentLogFileName('public test / v2'), 'public-test-v2-agent.log')
  assert.equal(agentLogFileName('***'), 'run-agent.log')
})
