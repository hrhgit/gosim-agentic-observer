/** One platform step of a run or version (prepare, execute, engine, score), from observer_diagnostics. */
export type Diagnostic = { kind: string; status: string; code: string; log: string; finished_at?: string | null }
/** The team's own agent.log for one run (portal action agent_log): its tail, or the whole file. */
export type AgentLogView = { available: boolean; bytes?: number; truncated?: boolean; log?: string }
/** A run is one card; a version's agent output is that of its public test run. */
export type LogTarget = { run_id: string } | { revision_id: string; test_run_id?: string | null }
export type Logs = {
  diagnostics: Diagnostic[]
  /** The run whose agent.log is shown and downloadable, if any. */
  agentRun: string | null
  /** null when the agent.log could not be read (shown as "not available" with the ZIP hint). */
  agent: AgentLogView | null
}
type Portal = <T>(action: string, fields: Record<string, unknown>) => Promise<T>

export function agentRunOf(target: LogTarget): string | null {
  return 'run_id' in target ? target.run_id : target.test_run_id || null
}

/**
 * Platform diagnostics and the agent's own output, read together. The agent.log is
 * a convenience: if it cannot be read, the diagnostics are still shown.
 */
export async function loadLogs(portal: Portal, target: LogTarget): Promise<Logs> {
  const agentRun = agentRunOf(target)
  const [diagnostics, agent] = await Promise.all([
    portal<Diagnostic[]>('diagnostics', 'run_id' in target ? { run_id: target.run_id } : { revision_id: target.revision_id }),
    agentRun ? portal<AgentLogView>('agent_log', { run_id: agentRun }).catch(() => null) : Promise.resolve(null),
  ])
  return { diagnostics: diagnostics ?? [], agentRun, agent }
}

/** File name for a downloaded agent.log: the card folder name used in result ZIPs, or the version's public test. */
export function agentLogFileName(stem: string): string {
  const safe = stem.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '')
  return (safe || 'run') + '-agent.log'
}
